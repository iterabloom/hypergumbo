# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ruby analysis pass using tree-sitter-ruby.

This analyzer uses tree-sitter to parse Ruby files and extract:
- Method definitions (def)
- Class declarations (class)
- Module declarations (module)
- Method call relationships
- Require/require_relative statements: a ``require_relative`` of a file the
  tree holds ends on that file's node (``_require_relative_target``); any
  other require ends on the ``ruby:<name>:0-0:file:file`` stand-in
- Rails callback edges. The recognised set is ``RAILS_CALLBACK_METHODS``:
  the controller action callbacks and their ``_filter`` spellings, the
  model lifecycle callbacks, the transaction callbacks (``after_commit``,
  ``after_create_commit``, ...) and ``validate``. Block-style callbacks
  are picked up too.
- ActiveRecord association ``references`` edges (has_many, belongs_to,
  has_one, has_and_belongs_to_many; ``meta["ref_construct"] = "association"``)
- Ruby delegate macro edges (delegate :method, to: :association)
- Rails / Sinatra route symbols, including namespaces, resources and
  member/collection blocks (``_extract_rails_routes``). Pass 3 skips
  files under spec/, test/, tests/, features/ and test-suite/
  (``_is_test_directory``); outside ``routes.rb`` and ``routes/``
  (``_is_route_definition_file``) it keeps only block-style (Sinatra)
  routes, those with ``has_block``
- Registration usage contexts (``_extract_registration_contexts``,
  WI-movur): a call outside every method that hands a framework LOCAL code
  by a symbol or a constant (``rescue_from E, with: :handler``,
  ``field :name``, ``subcommand "x", Klass``)
  gets a ``kind="call"`` context per such argument, ``symbol_ref`` the
  method or class, when a shipped ``usage:`` row names the call at that
  position (``framework_patterns.usage_row_names_call``). There is no list
  of DSL names here: the rows are the list.
- ``instantiates`` edges for ``.new`` (INV-kahig)
- ``event_publishes`` edges for ActiveJob / Sidekiq enqueue calls
- ``meta["parent_base_classes"]`` on a class's PUBLIC instance methods
  (``_public_member_bases``): the class's written superclass, for
  ``parent_base_class:`` rows (Thor commands, dry-cli ``#call``). Ruby's
  ``private`` / ``protected`` decide; not written on a ``def self.x`` or on a
  ``def`` inside a block of the class body
- ``extends`` inheritance edges, and mixin declarations recorded as
  ``meta["included_modules"]`` for the linker to resolve (WI-hatip)
- ``field`` / ``variable`` data anchors (``_emit_ruby_data_symbols``,
  WI-jusus)
- ``references`` edges for ``method(:name)`` first-class method
  references (``evidence_type="method_reference"``) and for hash-pair
  values naming a method, ``{on_success: cb}``
  (``evidence_type="hash_field_reference"``)
- Receiver-type tracking for a variable assigned a construction
  (``c = Conn.new``)
- One import table per file for the shared call resolution (ADR-0064,
  :func:`_ruby_import_scope`): empty, of namespaces
- Local-binding filtering (``_is_ruby_local``): a bare identifier bound
  earlier in its scope is a read, not a call

If tree-sitter with Ruby support is not installed, the analyzer
gracefully degrades: it emits a ``UserWarning`` and returns a skipped
result (``skip_reason_code=DEPENDENCY_UNAVAILABLE``).

How It Works
------------
1. Check if tree-sitter-ruby is available
2. If not available, warn and return a skipped result (not an error)
3. Multi-pass analysis — Ruby overrides ``analyze()`` because it needs
   more than the standard two passes:
   - Pass 1: Parse all files, extract all symbols into global registry.
     The registries (per file and run-wide) are keyed by a declaration's
     FULL LEXICAL PATH (``_registry_key``): ``A::B::X`` for a class or
     module, ``A::B::X#go`` / ``A::B::X.run`` for a method, the same for
     ``module A; module B; class X`` as for ``class A::B::X``. The key is
     not ``Symbol.name``, which for a block-nested declaration is the
     innermost name alone (``X``, ``X.run``) and would let ``A::X`` and
     ``A::B::X`` clobber each other; ``Symbol.name`` and ``qualified_name``
     are inputs of ``stable_id`` and stay as they are (INV-dosoz).
   - Pass 2: Detect calls and resolve against global symbol registry.
     A call in no method is anchored on the class or module whose body
     holds it (``_SCOPE_BODY_NODES``), else on the file (INV-bamij).
     A constant (a call's receiver, a superclass, a job class, the class a
     typed variable was assigned from) is resolved FIRST, in Ruby's order
     (``_resolve_constant``): a RELATIVE one through the call site's
     lexical nesting, innermost out, as ``Module.nesting`` does
     (``_lexical_nesting``, WI-gomup), then through the project ancestors
     of the innermost scope -- its superclass and included modules
     (``_constant_ancestors``) -- then at the top level; one written
     ``::X`` at the top level only. Only a full path matches. There is no
     fallback to a last segment: ``A::X`` never reaches ``A::B::X``,
     ``Net::HTTP`` never reaches a project's ``Postal::HTTP``, and a
     top-level ``File`` never reaches ``A::File``. The method is then
     looked up on that class alone, in the declarations of ONE file (two
     files defining ``A::X#go`` are Ruby's load order, which the source
     does not hold: ``_declared_once``).

     CALL RESOLUTION (ADR-0064). The analyzer binds a call only on the
     evidence Ruby's own lookup starts from: the constant's class found as
     above (``qualified``; ``receiver_type`` for a call on
     ``Const.new(...)`` or on a variable assigned one), and, for a send to
     ``self`` (``self.m``, a receiverless ``m(x)``, a bare ``m`` that is not
     a local), the enclosing class's own method declared in this file
     (``own_type``; ``_bare_send``). Every other call is REPORTED with its
     facts (``make_unresolved_edge``) and the shared linkers decide with
     the file's table: a project constant whose class lacks the method, or
     whose method two files declare, gives ``qualifier`` (``Const.m``,
     ``Const.new`` -> ``initialize``) or ``receiver_type_hint`` (a call on
     an instance) holding the class's FULL path, so the inherited-calls
     linker identifies the class by it and walks its ancestors; a send to
     ``self`` gives ``enclosing_class`` (the class's ``Symbol.name``, which
     the linker matches against the caller's own qualified name), and
     ``super`` adds ``super_receiver``. No call is looked up by its name
     among the repository's methods, nor in a table of the file's other
     names: a same-named method of another class, or a top-level ``def`` of
     another file, is not the callee by any rule of Ruby's. A constant the
     project does not declare (``JSON.parse``)
     yields an unresolved ``calls`` edge with
     ``meta["receiver"] = "constant_external"`` and an ``ExternalRef`` whose
     module is the constant as written, less a root ``::`` (``Net::HTTP``,
     the owner path ADR-0051 asks for; WI-surar). A send to ``self`` from
     a top-level method or the file (``puts "x"``), or of a name Kernel
     defines (:data:`_RUBY_KERNEL_NAMES`), takes the ``external`` module
     slot, ``ruby:external:0-0:<name>:unresolved`` (WI-napaf, python's
     INV-foluz residual shape), which the I/O catalogue's Kernel rows
     read; any other takes its class. A bare ``identifier`` -- also a
     call's receiver and a ``[]`` object (``current_user.name``,
     ``params[:id]``) -- is a send to ``self`` unless Ruby's lexical rule
     makes it a LOCAL (a parameter, block parameter, rescue / ``for``
     variable or an earlier assignment in the scope; ``_is_ruby_local``;
     WI-misir). A receiver nobody can type gets the ``external``
     placeholder with ``call_construct: method``. A stub whose name Ruby's
     core or Rails defines carries ``stdlib_member_name``
     (``_RUBY_LIBRARY_METHOD_NAMES``). Direct recursion emits its self
     edge. ``super``'s class is the one whose
     declaration contains it, by POSITION; its module slot is the
     superclass as written, read for a reopened ``class Foo`` that names
     none off the declaration of the same QUALIFIED name (``A::Foo``) that
     does (``_superclass_of``, WI-gikuk), never by the bare name, which
     drops the module nesting and would make ``A::Foo`` and ``B::Foo``
     share one.
     Each file's edges are then annotated with dataflow access modes
     (ADR-0015 Tier 1, ``_annotate_dataflow``).
   - Passes 2b / 2c / 2d and 3, plus a post-pass inheritance sweep
4. Detect method calls and require statements
5. Track variable types from constructions (``var = Class.new``) to
   resolve ``var.method`` → ``Class#method`` (``ast_call`` evidence
   carrying ``meta["resolution_quality"] = "typed_receiver"``)
6. Skip bare identifiers that Ruby's lexical rule makes LOCALS
   (parameters, block parameters, rescue / ``for`` variables, earlier
   assignments)

Why This Design
---------------
- Optional dependency keeps base install lightweight
- Uses tree-sitter-ruby package for grammar
- Multi-pass allows cross-file call resolution
- Same pattern as Go/Rust/Elixir/Java/PHP/C analyzers for consistency

Population of ``is_exported`` follows Ruby's default-public rule: top-level
methods, classes, and modules are exported; methods defined inside another
method (locally scoped) are not. ``private`` / ``protected`` directives
within a class are read for ``parent_base_classes`` only; they do not reach
``modifiers``, ``is_exported`` or the computed visibility yet.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
import posixpath
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Iterable, Iterator, Optional

from hypergumbo_core.dataflow import annotate_dataflow as _annotate_dataflow, get_dataflow_config as _get_dataflow_config
from hypergumbo_core.discovery import find_files
from hypergumbo_core.framework_patterns import usage_row_names_call
from hypergumbo_core.ir import (
    AnalysisRun, Edge, ExternalRef, PASS_VERSION, Span, Symbol, UsageContext,
    make_pass_id,
)
from hypergumbo_core.qualified_name_axis import separator_for_language
from hypergumbo_core import resolved_by
from hypergumbo_core.import_scope import ImportScope
from hypergumbo_core.symbol_resolution import NameResolver
from hypergumbo_core.analyze.base import (
    record_partial_parse,
    AnalysisResult,
    FileAnalysis,
    TreeSitterAnalyzer,
    find_child_by_type,
    iter_tree,
    file_anchor_symbol,
    enclosing_declared_symbol,
    make_file_id,
    make_route_symbol,
    make_symbol_id,
    make_unresolved_edge,
    node_text,
    populate_docstrings_from_tree,
    SymbolsAt,
    symbol_declared_by,
    symbols_at,
)
from hypergumbo_core.analyze.edge_target import in_tree_file_id
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.analyze.var_scope import ScopedVarTypes
from hypergumbo_lang_mainstream.symbol_introspection import (
    compute_cyclomatic_complexity,
    extract_preceding_doc_comment,
)
from hypergumbo_core.pass_silence import DEPENDENCY_UNAVAILABLE, PASS_CRASHED

if TYPE_CHECKING:
    import tree_sitter

PASS_ID = make_pass_id("ruby")

#: ADR-0039 ruling 6 grade of a send to ``self`` bound to the enclosing
#: class's own method: exact.
_RUBY_SAME_CLASS_GRADE = 1.0

#: Scopes whose BODY anchors a call in no method (class-body ``include``, a
#: Rails ``has_many``; INV-bamij).
_SCOPE_BODY_NODES: frozenset[str] = frozenset({"class", "module", "singleton_class"})


#: THE SCREEN OF LIBRARY NAMES (ADR-0064), part 1: the methods Ruby's ``Kernel``
#: module defines, which every object reaches without a receiver (``puts``,
#: ``raise``, ``require``, ``system``, ``format``), and the public methods every
#: object has from ``Kernel``, ``Object`` and ``BasicObject`` (``send``,
#: ``respond_to?``, ``tap``, ``to_s``). Taken from the Ruby 3.3 core
#: reference, docs.ruby-lang.org/en/3.3/: the pages ``Kernel``, ``Object`` and
#: ``BasicObject`` (public and private instance methods, module functions);
#: NOT from any repository. A send to ``self`` of one of these names that the
#: enclosing class does not declare in its own file goes out with the
#: ``external`` module (:func:`_bare_send`): its owner may be Kernel, and the
#: I/O catalogue's Kernel rows (``system``, ``open``, ``spawn``) must still
#: reach it.
_RUBY_KERNEL_NAMES: frozenset[str] = frozenset({
    # Kernel: module functions and private instance methods
    "Array", "Complex", "Float", "Hash", "Integer", "Rational", "String",
    "__callee__", "__dir__", "__method__", "abort", "at_exit", "autoload",
    "autoload?", "binding", "block_given?", "callcc", "caller",
    "caller_locations", "catch", "eval", "exec", "exit", "exit!", "fail",
    "fork", "format", "gets", "global_variables", "iterator?", "lambda",
    "load", "local_variables", "loop", "open", "p", "pp", "print", "printf",
    "proc", "putc", "puts", "raise", "rand", "readline", "readlines",
    "require", "require_relative", "select", "set_trace_func", "sleep",
    "spawn", "sprintf", "srand", "syscall", "system", "test", "throw",
    "trace_var", "trap", "untrace_var", "warn",
    # Kernel / Object: public instance methods
    "class", "clone", "define_singleton_method", "display", "dup",
    "enum_for", "eql?", "extend", "freeze", "frozen?", "hash", "inspect",
    "instance_of?", "instance_variable_defined?", "instance_variable_get",
    "instance_variable_set", "instance_variables", "is_a?", "itself",
    "kind_of?", "method", "methods", "nil?", "object_id", "private_methods",
    "protected_methods", "public_method", "public_methods", "public_send",
    "remove_instance_variable", "respond_to?", "respond_to_missing?", "send",
    "singleton_class", "singleton_method", "singleton_methods", "tap", "then",
    "to_enum", "to_s", "yield_self",
    # BasicObject
    "__id__", "__send__", "equal?", "initialize", "instance_eval",
    "instance_exec", "method_missing", "singleton_method_added",
    "singleton_method_removed", "singleton_method_undefined",
})

#: THE SCREEN OF LIBRARY NAMES (ADR-0064), part 2: the methods the core
#: classes and modules define on their instances and on themselves. A call
#: ``x.m`` on a receiver this analyzer cannot type whose ``m`` is here
#: (``items.each``, ``s.strip``, ``h.merge``, ``f.read``) is the library's at
#: least as likely as the repository's one declaration of ``m``: it goes out
#: with ``stdlib_member_name``, and the single-holder name linker does not
#: bind it. Taken from the Ruby 3.3 core reference,
#: docs.ruby-lang.org/en/3.3/<Class>.html, one page per group below (public
#: instance and class methods; operator methods left out, since no call
#: written with an operator reaches a stub); NOT from any repository.
_RUBY_CORE_METHOD_NAMES: frozenset[str] = _RUBY_KERNEL_NAMES | frozenset({
    # Module (attr_*, include, define_method, const_get, ...; also private)
    "alias_method", "ancestors", "append_features", "attr", "attr_accessor",
    "attr_reader", "attr_writer", "class_eval", "class_exec",
    "class_variable_defined?", "class_variable_get", "class_variable_set",
    "class_variables", "const_added", "const_defined?", "const_get",
    "const_missing", "const_set", "const_source_location", "constants",
    "define_method", "deprecate_constant", "extend_object", "extended",
    "include", "include?", "included", "included_modules", "instance_method",
    "instance_methods", "method_added", "method_defined?", "method_removed",
    "method_undefined", "module_eval", "module_exec", "module_function",
    "name", "prepend", "prepend_features", "prepended", "private",
    "private_class_method", "private_constant", "private_instance_methods",
    "private_method_defined?", "protected", "protected_instance_methods",
    "protected_method_defined?", "public", "public_class_method",
    "public_instance_method", "public_instance_methods",
    "public_method_defined?", "refine", "remove_class_variable",
    "remove_const", "remove_method", "ruby2_keywords", "set_temporary_name",
    "undef_method", "undefined_instance_methods", "using",
    # Class
    "allocate", "attached_object", "inherited", "new", "subclasses",
    "superclass",
    # Comparable
    "between?", "clamp",
    # Enumerable
    "all?", "any?", "chain", "chunk", "chunk_while", "collect",
    "collect_concat", "compact", "count", "cycle", "detect", "drop",
    "drop_while", "each_cons", "each_entry", "each_slice", "each_with_index",
    "each_with_object", "entries", "filter", "filter_map", "find", "find_all",
    "find_index", "first", "flat_map", "grep", "grep_v", "group_by",
    "inject", "lazy", "map", "max", "max_by", "member?", "min", "min_by",
    "minmax", "minmax_by", "none?", "one?", "partition", "reduce", "reject",
    "reverse_each", "slice_after", "slice_before", "slice_when", "sort",
    "sort_by", "sum", "take", "take_while", "tally", "to_a", "to_h",
    "to_set", "uniq", "zip",
    # Array
    "append", "assoc", "at", "bsearch", "bsearch_index", "clear", "collect!",
    "combination", "compact!", "concat", "deconstruct", "delete",
    "delete_at", "delete_if", "difference", "dig", "each", "each_index",
    "empty?", "fetch", "fill", "filter!", "flatten", "flatten!", "index",
    "insert", "intersect?", "intersection", "join", "keep_if", "last",
    "length", "map!", "pack", "permutation", "pop", "product", "push",
    "rassoc", "reject!", "repeated_combination", "repeated_permutation",
    "replace", "reverse", "reverse!", "rindex", "rotate", "rotate!",
    "sample", "select!", "shift", "shuffle", "shuffle!", "size", "slice",
    "slice!", "sort!", "sort_by!", "to_ary", "transpose", "try_convert",
    "union", "uniq!", "unshift", "values_at",
    # Hash
    "compare_by_identity", "compare_by_identity?", "deconstruct_keys",
    "default", "default_proc", "each_key", "each_pair", "each_value",
    "except", "fetch_values", "has_key?", "has_value?", "invert", "key",
    "key?", "keys", "merge", "merge!", "rehash", "store", "to_hash",
    "transform_keys", "transform_keys!", "transform_values",
    "transform_values!", "update", "value?", "values",
    # String
    "ascii_only?", "b", "bytes", "bytesize", "byteindex", "byterindex",
    "byteslice", "bytesplice", "capitalize", "capitalize!", "casecmp",
    "casecmp?", "center", "chars", "chomp", "chomp!", "chop", "chop!", "chr",
    "codepoints", "crypt", "delete!", "delete_prefix", "delete_prefix!",
    "delete_suffix", "delete_suffix!", "downcase", "downcase!", "dump",
    "each_byte", "each_char", "each_codepoint", "each_grapheme_cluster",
    "each_line", "encode", "encode!", "encoding", "end_with?",
    "force_encoding", "getbyte", "grapheme_clusters", "gsub", "gsub!", "hex",
    "intern", "lines", "ljust", "lstrip", "lstrip!", "match", "match?",
    "next", "next!", "oct", "ord", "rjust", "rpartition", "rstrip",
    "rstrip!", "scan", "scrub", "scrub!", "setbyte", "split", "squeeze",
    "squeeze!", "start_with?", "strip", "strip!", "sub", "sub!", "succ",
    "succ!", "swapcase", "swapcase!", "to_c", "to_f", "to_i", "to_r",
    "to_str", "to_sym", "tr", "tr!", "tr_s", "tr_s!", "undump",
    "unicode_normalize", "unicode_normalize!", "unicode_normalized?",
    "unpack", "unpack1", "upcase", "upcase!", "upto", "valid_encoding?",
    # Symbol
    "all_symbols", "id2name", "to_proc",
    # Integer and Numeric
    "abs", "abs2", "allbits?", "angle", "anybits?", "arg", "bit_length",
    "ceil", "ceildiv", "coerce", "conj", "conjugate", "denominator",
    "digits", "div", "divmod", "downto", "even?", "fdiv", "finite?",
    "floor", "gcd", "gcdlcm", "imaginary", "infinite?", "integer?", "lcm",
    "magnitude", "modulo", "negative?", "nobits?", "nonzero?", "numerator",
    "odd?", "phase", "polar", "positive?", "pow", "pred", "quo",
    "rationalize", "real?", "rect", "rectangular", "remainder", "round",
    "sqrt", "step", "times", "to_int", "truncate", "zero?",
    # Float
    "nan?", "next_float", "prev_float",
    # Range
    "begin", "cover?", "end", "exclude_end?", "overlap?",
    # Proc
    "arity", "call", "curry", "lambda?", "parameters", "source_location",
    "yield",
    # IO (instance and class methods)
    "advise", "autoclose?", "binmode", "binmode?", "binread", "binwrite",
    "close", "close_on_exec?", "close_read", "close_write", "closed?",
    "copy_stream", "eof", "eof?", "external_encoding", "fcntl", "fdatasync",
    "fileno", "flush", "for_fd", "foreach", "fsync", "getc",
    "internal_encoding", "ioctl", "isatty", "lineno", "pid", "pipe",
    "popen", "pos", "pread", "pwrite", "read", "read_nonblock", "readbyte",
    "readchar", "readpartial", "reopen", "rewind", "seek", "set_encoding",
    "set_encoding_by_bom", "stat", "sync", "sysopen", "sysread", "sysseek",
    "syswrite", "tell", "timeout", "to_io", "tty?", "ungetbyte", "ungetc",
    "wait", "wait_priority", "wait_readable", "wait_writable", "write",
    "write_nonblock",
    # File (instance and class methods)
    "absolute_path", "absolute_path?", "atime", "basename", "birthtime",
    "blockdev?", "chardev?", "chmod", "chown", "ctime", "directory?",
    "dirname", "executable?", "executable_real?", "exist?", "expand_path",
    "extname", "file?", "flock", "fnmatch", "fnmatch?", "ftype",
    "grpowned?", "identical?", "lchmod", "lchown", "link", "lstat",
    "lutime", "mkfifo", "mtime", "owned?", "path", "pipe?", "readable?",
    "readable_real?", "readlink", "realdirpath", "realpath", "rename",
    "setgid?", "setuid?", "size?", "socket?", "sticky?", "symlink",
    "symlink?", "to_path", "umask", "unlink", "utime", "world_readable?",
    "world_writable?", "writable?", "writable_real?",
    # Struct
    "keyword_init?", "members",
    # Exception
    "backtrace", "backtrace_locations", "cause", "detailed_message",
    "exception", "full_message", "message", "set_backtrace", "to_tty?",
})

#: THE SCREEN OF LIBRARY NAMES (ADR-0064), part 3: Rails. ActiveSupport adds
#: methods to the core classes themselves (``blank?``, ``present?``,
#: ``squish``, ``deep_merge``, ``index_by``), and an ActiveRecord model or
#: relation answers ``where``, ``find_by``, ``pluck``, ``update!``, whatever the
#: repository declares; an untyped receiver in a Rails application is such a
#: value as often as not. Taken from the Rails 7.1 API reference,
#: api.rubyonrails.org, the pages named in each group below; NOT from any
#: repository. A name here and in no core page is screened only because of
#: Rails (the results file of the batch says what that changed).
_RAILS_METHOD_NAMES: frozenset[str] = frozenset({
    # ActiveSupport core extensions: Object (blank, try, inclusion, deep_dup,
    # to_param / to_query, json, instance variables)
    "acts_like?", "as_json", "blank?", "deep_dup", "duplicable?", "in?",
    "instance_values", "instance_variable_names", "presence", "presence_in",
    "present?", "to_param", "to_query", "try", "try!", "with_options",
    # ActiveSupport core extensions: String (inflections, filters, access,
    # conversions, output safety)
    "camelcase", "camelize", "classify", "constantize", "dasherize",
    "deconstantize", "demodulize", "exclude?", "foreign_key", "from",
    "html_safe", "html_safe?", "humanize", "indent", "indent!", "inquiry",
    "mb_chars", "parameterize", "pluralize", "remove", "remove!",
    "safe_constantize", "singularize", "squish", "squish!", "strip_heredoc",
    "tableize", "titlecase", "titleize", "to", "to_date", "to_datetime",
    "to_time", "truncate_words", "underscore", "upcase_first",
    # ActiveSupport core extensions: Array, Hash, Enumerable
    "assert_valid_keys", "compact_blank", "compact_blank!", "deep_merge",
    "deep_merge!", "deep_stringify_keys", "deep_symbolize_keys",
    "deep_transform_keys", "deep_transform_values", "except!", "excluding",
    "extract!", "extract_options!", "fifth", "forty_two", "fourth",
    "in_groups", "in_groups_of", "including", "index_by", "index_with",
    "many?", "maximum", "minimum", "pick", "pluck", "reverse_merge",
    "reverse_merge!", "second", "second_to_last", "slice!", "sole",
    "stringify_keys", "stringify_keys!", "symbolize_keys",
    "symbolize_keys!", "third", "third_to_last", "to_formatted_s", "to_fs",
    "to_options", "to_sentence", "with_indifferent_access", "without",
    # ActiveSupport core extensions: Numeric, Integer, Date and Time
    "ago", "beginning_of_day", "beginning_of_hour", "beginning_of_month",
    "beginning_of_week", "beginning_of_year", "bytes", "day", "days",
    "end_of_day", "end_of_hour", "end_of_month", "end_of_week",
    "end_of_year", "from_now", "gigabytes", "hour", "hours", "in_time_zone",
    "iso8601", "kilobytes", "megabytes", "minute", "minutes", "month",
    "months", "multiple_of?", "ordinalize", "seconds", "since",
    "until", "week", "weeks", "year", "years",
    # ActiveSupport: Module (delegation, attribute accessors), Concern
    "class_attribute", "class_methods", "cattr_accessor", "cattr_reader",
    "cattr_writer", "delegate", "delegate_missing_to", "mattr_accessor",
    "mattr_reader", "mattr_writer", "module_parent",
    # ActiveRecord::QueryMethods, FinderMethods, Calculations, SpawnMethods,
    # Relation, Batches (on a model class and on a relation)
    "all", "average", "create_or_find_by", "create_with", "distinct",
    "eager_load", "exists?", "extending", "find_by", "find_by!",
    "find_each", "find_in_batches", "find_or_create_by",
    "find_or_create_by!", "find_or_initialize_by", "group", "having",
    "ids", "in_batches", "includes", "joins", "left_joins",
    "left_outer_joins", "limit", "lock", "none", "not", "offset", "or",
    "order", "preload", "readonly", "references", "regroup", "reorder",
    "reselect", "rewhere", "scoping", "take!", "unscope", "unscoped",
    "where",
    # ActiveRecord::Persistence, Relation (bulk), Callbacks, Transactions
    "becomes", "build", "create", "create!", "decrement", "decrement!",
    "delete_all", "destroy", "destroy!", "destroy_all", "destroyed?",
    "increment", "increment!", "insert_all", "insert_all!", "new_record?",
    "persisted?", "previously_new_record?", "reload", "save", "save!",
    "toggle", "toggle!", "touch", "touch_all", "transaction", "update!",
    "update_all", "update_attribute", "update_column", "update_columns",
    "upsert", "upsert_all", "with_lock",
    # ActiveRecord::AttributeMethods (and Dirty), ActiveModel::Validations
    "assign_attributes", "attribute_changed?", "attribute_names",
    "attributes", "changed", "changed?", "changes", "errors",
    "has_attribute?", "invalid?", "previous_changes", "read_attribute",
    "saved_changes", "valid?", "validate!", "write_attribute",
    # ActiveJob::Core and Enqueuing
    "perform_later", "perform_now",
    # ActionController::Metal, Base: rendering, redirecting, request data
    "cookies", "flash", "head", "params", "redirect_to", "render",
    "render_to_string", "request", "respond_to", "response", "session",
    "url_for",
})

#: What a stub's ``stdlib_member_name`` is read from: core and Rails.
_RUBY_LIBRARY_METHOD_NAMES: frozenset[str] = _RUBY_CORE_METHOD_NAMES | _RAILS_METHOD_NAMES

# HTTP methods for Rails/Sinatra route detection (used by UsageContext extraction)
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "match"}

# Rails callback methods that create implicit call edges from class to method.
# Covers both controller callbacks (before_action) and model lifecycle callbacks
# (before_save, after_create, validate, etc.). Includes legacy Rails 3 API names.
RAILS_CALLBACK_METHODS = frozenset({
    # Controller callbacks
    "before_action", "after_action", "around_action",
    "before_filter", "after_filter", "around_filter",
    # Model lifecycle callbacks (ActiveRecord::Callbacks)
    "before_save", "after_save", "around_save",
    "before_create", "after_create", "around_create",
    "before_update", "after_update", "around_update",
    "before_destroy", "after_destroy", "around_destroy",
    "before_validation", "after_validation",
    "after_initialize", "after_find", "after_touch",
    # Transaction callbacks (ActiveRecord::Transactions)
    "after_commit", "after_rollback",
    "after_create_commit", "after_update_commit", "after_destroy_commit",
    "after_save_commit",
    # Custom validation method registration
    "validate",
})

# ActiveRecord association macros that create model-to-model relationships.
_ASSOCIATION_METHODS = frozenset({
    "has_many", "belongs_to", "has_one", "has_and_belongs_to_many",
})

# Directories that contain test files where get/post/delete are HTTP test helpers,
# not route definitions.
_TEST_DIR_NAMES = frozenset({"spec", "test", "tests", "features", "test-suite"})


def _is_test_directory(file_path: Path, repo_root: Path) -> bool:
    """Check if file is inside a test/spec directory.

    Returns True for files under spec/, test/, tests/, features/, test-suite/.
    These directories use HTTP method helpers (get, post, delete) for making
    test requests, NOT for defining routes.
    """
    try:
        rel = file_path.relative_to(repo_root)
    except ValueError:  # pragma: no cover — files always under repo_root
        return False  # pragma: no cover
    return bool(_TEST_DIR_NAMES & set(rel.parts))


def _is_route_definition_file(file_path: Path, repo_root: Path) -> bool:
    """Check if file is a route definition file.

    Returns True for:
    - Files named routes.rb (e.g., config/routes.rb)
    - Files inside a routes/ directory (e.g., config/routes/api.rb)

    Rails routes are defined in config/routes.rb and optionally split into
    config/routes/*.rb partials. Matching by name/directory catches both
    standard and non-standard layouts.
    """
    if file_path.name == "routes.rb":
        return True
    try:
        rel = file_path.relative_to(repo_root)
    except ValueError:  # pragma: no cover — files always under repo_root
        return False  # pragma: no cover
    return "routes" in rel.parts


def find_ruby_files(repo_root: Path) -> Iterator[Path]:
    """Yield all Ruby files in the repository."""
    yield from find_files(repo_root, ["*.rb"])


def is_ruby_tree_sitter_available() -> bool:
    """Check if tree-sitter with Ruby grammar is available.

    Delegates to the RubyAnalyzer singleton (defined at module level below).
    This function is kept for backward compatibility.
    """
    return _analyzer._check_grammar_available()


def _find_child_by_field(node: "tree_sitter.Node", field_name: str) -> Optional["tree_sitter.Node"]:
    """Find child by field name."""
    return node.child_by_field_name(field_name)


def _snake_to_pascal(name: str) -> str:
    """Convert snake_case to PascalCase.

    Examples:
        user_service -> UserService
        http_client -> HttpClient
        api -> Api
    """
    return "".join(word.capitalize() for word in name.split("_"))


def _extract_require_hints(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> dict[str, str]:
    """Extract require/require_relative statements and infer class/module names.

    Ruby convention maps snake_case file paths to PascalCase class names.
    For example:
        require 'user_service' -> hints that UserService class comes from this path
        require 'math/calculator' -> hints that Calculator class comes from this path

    Returns a dict mapping inferred class/module names to their require paths.
    """
    hints: dict[str, str] = {}

    for node in iter_tree(tree.root_node):
        if node.type != "call":
            continue

        # Get method name
        method_node = None
        for child in node.children:
            if child.type == "identifier":
                method_node = child
                break

        if not method_node:  # pragma: no cover - call nodes always have identifier
            continue

        callee_name = node_text(method_node, source)
        if callee_name not in ("require", "require_relative"):
            continue

        # Extract the require path from arguments
        args_node = _find_child_by_field(node, "arguments")
        if not args_node:  # pragma: no cover - require always has arguments
            continue

        for arg in args_node.children:
            if arg.type == "string":
                content_node = find_child_by_type(arg, "string_content")
                if content_node:
                    require_path = node_text(content_node, source)
                    # Extract the last component and convert to PascalCase
                    # 'math/calculator' -> 'calculator' -> 'Calculator'
                    basename = require_path.rsplit("/", 1)[-1]
                    # Remove .rb extension if present
                    if basename.endswith(".rb"):
                        basename = basename[:-3]
                    if basename:
                        class_name = _snake_to_pascal(basename)
                        hints[class_name] = require_path

    return hints


def _get_ruby_class_or_module_chain(
    node: "tree_sitter.Node", source: bytes
) -> list[str]:
    """Walk up the tree collecting all enclosing class/module names.

    Returns the chain from outermost to innermost (excluding the current
    node itself). Used to build qualified names for nested types.
    """
    chain: list[str] = []
    current = node.parent
    while current is not None:
        if current.type in ("class", "module"):
            name_node = _find_child_by_field(current, "name")
            if name_node:
                chain.append(node_text(name_node, source))
        current = current.parent
    return list(reversed(chain))


def _make_ruby_qualified_name(
    chain: list[str], name: str
) -> str:
    """Build a Ruby qualified name using ``::`` separator.

    Ruby's runtime convention is ``Mod::Class#instance_method`` /
    ``Mod::Class.class_method``, but ``Symbol.qualified_name`` records a
    coarse-grained identifier — the instance/class distinction lives on
    ``Symbol.name`` and modifiers. We canonicalise to the ``::`` form here.
    """
    sep = separator_for_language("ruby")  # "::"
    parts: list[str] = list(chain)
    parts.append(name)
    return sep.join(parts)


def _get_enclosing_class_or_module(node: "tree_sitter.Node", source: bytes) -> tuple[Optional[str], str]:
    """Walk up the tree to find the enclosing class or module name.

    Returns (name, type) where type is 'class' or 'module'.
    """
    current = node.parent
    while current is not None:
        if current.type == "class":
            name_node = _find_child_by_field(current, "name")
            if name_node:
                return node_text(name_node, source), "class"
        elif current.type == "module":
            name_node = _find_child_by_field(current, "name")
            if name_node:
                return node_text(name_node, source), "module"
        current = current.parent
    return None, ""  # pragma: no cover - defensive


def _lexical_path(written: str) -> str:
    """The constant path a ``::``-joined chain of names, as written, declares.

    ``module A; class X`` (chain ``A::X``) and ``class A::X`` both give
    ``A::X``. A name written with a root ``::`` restarts the path, as it does
    in Ruby: ``module M; class ::Foo::Bar`` (chain ``M::::Foo::Bar``) declares
    ``Foo::Bar``.
    """
    return written.rsplit("::::", 1)[-1].lstrip(":")


def _namespaces(path: str) -> list[str]:
    """``A::B::C`` -> ``["A::B::C", "A::B", "A"]``; ``""`` -> ``[]``."""
    parts = path.split("::") if path else []
    return ["::".join(parts[:i]) for i in range(len(parts), 0, -1)]


def _owner_path(symbol: Symbol) -> Optional[str]:
    """The lexical path of the class or module that owns a method symbol.

    ``None`` for a top-level method and for a symbol that is not a method.
    Read off ``qualified_name``, which carries the whole enclosing chain
    (``A::B::X::run``); ``Symbol.name`` carries only the innermost name as
    written (``X.run``) and so is shared by ``A::X`` and ``A::B::X``.
    """
    qualified = symbol.qualified_name or ""
    if symbol.kind != "method" or "::" not in qualified:
        return None
    return _lexical_path(qualified.rsplit("::", 1)[0])


def _registry_key(symbol: Symbol) -> str:
    """The key a declaration takes in the name registries: its FULL lexical
    path (INV-dosoz).

    A class or module is keyed ``A::B::X`` whether it is declared in blocks
    (``module A; module B; class X``) or compactly (``class A::B::X``), and a
    method ``A::B::X#go`` / ``A::B::X.run``. ``Symbol.name`` cannot be the
    key: for a block-nested declaration it is the innermost name alone
    (``X``, ``X.run``), so two classes of one name in different namespaces
    would share a key and the later registration would take every reference
    to either. ``Symbol.name`` itself is left as it is, because it is an input
    of the symbol's ``stable_id``.
    """
    if symbol.kind == "method":
        owner = _owner_path(symbol)
        if owner is None:
            return symbol.name
        short = (symbol.qualified_name or "").rsplit("::", 1)[1]
        return f"{owner}{'#' if '#' in symbol.name else '.'}{short}"
    return _lexical_path(symbol.qualified_name or symbol.name)


def _declaration_nesting(symbol: Symbol) -> list[str]:
    """The lexical scopes a class declaration sits in, innermost first: where
    its superclass name is looked up before the top level.

    ``module A; class Q < X`` gives ``["A"]``; a compact ``class A::Q < X`` at
    top level gives ``[]`` (Ruby does not add ``A`` to the nesting). Read off
    the symbol: ``qualified_name`` less the name as written is the enclosing
    chain. An ENCLOSING scope that was itself declared compactly
    (``module A::B; class Q < X``) is one nesting level in Ruby but is split
    into ``A::B`` and ``A`` here, because the chain does not record where its
    own ``::`` came from.
    """
    qualified = symbol.qualified_name or symbol.name
    suffix = f"::{symbol.name}"
    if not qualified.endswith(suffix):
        return []
    return _namespaces(_lexical_path(qualified[: -len(suffix)]))


def _constant_candidates(written: str, scopes: Iterable[str]) -> list[str]:
    """The full paths a constant reference can name, in the order Ruby tries
    them: under each of ``scopes``, then at the top level. A reference written
    with a root ``::`` is absolute and names the top-level path only."""
    path = written.lstrip(":")
    if written.startswith("::"):
        return [path]
    return [*(f"{scope}::{path}" for scope in scopes), path]


def _resolve_constant(
    written: str,
    nesting: Iterable[str],
    global_symbols: dict[str, Symbol],
    ancestors: Optional[dict[str, list[str]]] = None,
    skip: Optional[str] = None,
) -> Optional[Symbol]:
    """The class or module a constant reference names, or ``None`` when the
    project declares none there.

    Ruby's own order: each lexical nesting level qualifying the name,
    innermost out (``X`` inside ``module A; module B`` is ``A::B::X``, else
    ``A::X``); then the ancestors of the innermost level, when ``ancestors``
    (:func:`_constant_ancestors`) is given -- a class reaches the constants of
    its superclass and of the modules it includes; then the top level. A
    reference written with a root ``::`` is absolute and skips all but the
    last. Only an exact registered path matches: there is no fallback to the
    last segment, so ``A::X`` never reaches ``A::B::X`` and a top-level ``X``
    never reaches either (INV-dosoz).

    ``skip`` is a path that cannot be the answer: the class being declared,
    for its own superclass (``module Api; class Base < Base``).
    """
    scopes = list(nesting)
    if scopes and ancestors:
        scopes.extend(ancestors.get(scopes[0], ()))
    for candidate in _constant_candidates(written, scopes):
        found = global_symbols.get(candidate)
        if (
            candidate != skip
            and found is not None and found.kind in ("class", "module")
        ):
            return found
    return None


def _constant_ancestors(
    symbols: Iterable[Symbol], global_symbols: dict[str, Symbol],
) -> dict[str, list[str]]:
    """``{class or module path: the paths of its project ancestors}``, nearest
    first: its superclass and the modules it includes (or extends), and
    theirs in turn.

    Read off every declaration of the path (a reopened class adds to one
    entry). An ancestor's name is resolved where it is written: a superclass
    in the scopes the class is declared in, an ``include`` inside the class
    as well. Ancestors the project does not declare are absent.
    """
    direct: dict[str, list[str]] = {}
    for sym in symbols:
        if sym.kind not in ("class", "module"):
            continue
        meta = sym.meta or {}
        own = _registry_key(sym)
        outer = _declaration_nesting(sym)
        written = [
            *((name, [own, *outer]) for name in meta.get("included_modules", ())),
            *((name, outer) for name in meta.get("base_classes", ())),
        ]
        for name, nesting in written:
            found = _resolve_constant(str(name), nesting, global_symbols, skip=own)
            if found is not None:
                parents = direct.setdefault(own, [])
                if _registry_key(found) not in parents:
                    parents.append(_registry_key(found))
    closed: dict[str, list[str]] = {}
    for own, parents in direct.items():
        seen: list[str] = []
        queue = list(parents)
        while queue:
            path = queue.pop(0)
            if path != own and path not in seen:
                seen.append(path)
                queue.extend(direct.get(path, ()))
        closed[own] = seen
    return closed


def _get_enclosing_method(
    node: "tree_sitter.Node",
    source: bytes,
    decl_index: SymbolsAt,
) -> Optional[Symbol]:
    """The method (instance or class method) whose ``def`` contains ``node``.

    Keyed by the declaration's POSITION, not its name (INV-midag). Ruby lets one
    file define a method twice, in a reopened class or as a redefinition, and a
    lookup by name would return whichever registered last. The ``def`` node IS
    the symbol's node.
    """
    current = node.parent
    while current is not None:
        if current.type in ("method", "singleton_method"):
            sym = symbol_declared_by(current, decl_index)
            if sym is not None:
                return sym
        current = current.parent
    return None  # pragma: no cover - defensive


def _is_nested_in_method(node: "tree_sitter.Node") -> bool:
    """Return True if ``node`` is lexically inside another method definition.

    Used to populate ``Symbol.is_exported``: a Ruby ``def`` that lives inside
    another ``def`` is locally scoped (not public API), whereas a top-level
    or class-body ``def`` defaults to public.
    """
    current = node.parent
    while current is not None:
        if current.type in ("method", "singleton_method"):
            return True
        current = current.parent
    return False


# Ruby's own rule for telling a local-variable read from a no-argument
# implicit-self call, which tree-sitter-ruby parses as the same bare
# ``identifier``: a name is a LOCAL iff a parameter, block parameter, rescue /
# for variable or assignment binds it earlier in the same scope. A ``def`` /
# ``class`` / ``module`` starts a fresh scope; a block sees its outer scope,
# and what a block binds is visible only inside that block (WI-misir).
_RUBY_SCOPE_ROOTS = frozenset({
    "method", "singleton_method", "class", "module", "singleton_class", "program",
})
_RUBY_BLOCK_SCOPES = frozenset({"block", "do_block", "lambda"})
_RUBY_PARAM_PARENTS = frozenset({
    "method_parameters", "lambda_parameters", "block_parameters",
    "splat_parameter", "hash_splat_parameter", "block_parameter",
    "destructured_parameter",
})
_RUBY_LHS_PARENTS = frozenset({
    "left_assignment_list", "destructured_left_assignment", "rest_assignment",
})


#: A class body's visibility calls: a bare one sets the visibility of the
#: ``def``s after it, with symbol arguments it sets the methods named.
_RUBY_VISIBILITY_CALLS = frozenset({"private", "protected", "public"})


def _public_member_bases(method: "tree_sitter.Node", source: bytes) -> list[str]:
    """The written bases of the class whose body declares ``method`` as a
    PUBLIC instance method; ``[]`` for any other ``def`` (WI-movur).

    Written as ``meta["parent_base_classes"]`` (the key Python, Java and
    Objective-C write) so a ``parent_base_class:`` row can name the members a
    framework dispatches by name: Thor runs a ``Thor`` subclass's public
    instance methods as commands, dry-cli a command's ``#call``. Ruby's own
    visibility decides: a bare ``private`` / ``protected`` covers the ``def``s
    after it until ``public``, and ``private :name`` or ``private def name``
    covers one. A ``def`` in a block of the class body (Thor's ``no_commands
    do``, a concern's ``included do``) is not a member the class declares
    there, and a ``def self.x`` is not an instance method. The base is the
    superclass as written (as on the class symbol), not its ancestors.
    """
    body = method.parent
    klass = body.parent if body is not None else None
    if body is None or klass is None or body.type != "body_statement" or klass.type != "class":
        return []
    superclass = _find_child_by_field(klass, "superclass")
    bases = [
        node_text(child, source)
        for child in (superclass.children if superclass is not None else ())
        if child.is_named
    ][:1]
    name_node = _find_child_by_field(method, "name")
    name = node_text(name_node, source) if name_node is not None else ""
    visibility = "public"
    for statement in body.named_children:
        if statement.start_byte >= method.start_byte:
            break
        text = node_text(statement, source)
        if statement.type == "identifier" and text in _RUBY_VISIBILITY_CALLS:
            visibility = text
    for statement in body.named_children:
        callee = statement.child_by_field_name("method") if statement.type == "call" else None
        args = statement.child_by_field_name("arguments") if callee is not None else None
        if (
            callee is not None and args is not None
            and node_text(callee, source) in ("private", "protected")
            and any(
                node_text(arg, source).lstrip(":") == name
                for arg in args.named_children if arg.type == "simple_symbol"
            )
        ):
            visibility = node_text(callee, source)
    return bases if visibility == "public" else []


def _ruby_binds(ident: "tree_sitter.Node") -> bool:
    """Whether this ``identifier`` IS a binding site (it introduces a local)."""
    parent = ident.parent
    if parent is None:  # pragma: no cover - an identifier always has a parent
        return False
    kind = parent.type
    if kind in ("assignment", "operator_assignment"):
        return parent.child_by_field_name("left") == ident
    if kind in ("optional_parameter", "keyword_parameter"):
        return parent.child_by_field_name("name") == ident
    if kind == "for":
        return parent.child_by_field_name("pattern") == ident
    return kind in _RUBY_PARAM_PARENTS or kind in _RUBY_LHS_PARENTS or kind == "exception_variable"


def _ruby_scope_bindings(
    root: "tree_sitter.Node", source: bytes,
) -> list[tuple[str, int, int, int]]:
    """``(name, at, visible_from, visible_to)`` for each binding in ``root``'s
    scope, not descending into nested scopes."""
    found: list[tuple[str, int, int, int]] = []
    stack = list(root.children)
    while stack:
        node = stack.pop()
        if node.type in _RUBY_SCOPE_ROOTS:
            continue
        if node.type == "identifier" and _ruby_binds(node):
            visible = (root.start_byte, root.end_byte)
            if node.parent is not None and node.parent.type != "method_parameters":
                ancestor: "tree_sitter.Node | None" = node.parent
                while ancestor is not None and ancestor != root:
                    if ancestor.type in _RUBY_BLOCK_SCOPES:
                        visible = (ancestor.start_byte, ancestor.end_byte)
                        break
                    ancestor = ancestor.parent
            found.append((node_text(node, source), node.start_byte, *visible))
        stack.extend(node.children)
    return found


def _is_ruby_local(
    ident: "tree_sitter.Node",
    source: bytes,
    cache: dict[tuple[int, int], list[tuple[str, int, int, int]]],
) -> bool:
    """Whether a bare ``identifier`` reads a LOCAL (WI-misir; see above)."""
    root = ident.parent
    while root is not None and root.type not in _RUBY_SCOPE_ROOTS:
        root = root.parent
    if root is None:  # pragma: no cover - the program node encloses everything
        return False
    key = (root.start_byte, root.end_byte)
    if key not in cache:
        cache[key] = _ruby_scope_bindings(root, source)
    name, at = node_text(ident, source), ident.start_byte
    return any(
        n == name and bound <= at and lo <= at < hi
        for n, bound, lo, hi in cache[key]
    )


def _method_short_name(method: Symbol) -> str:
    """``K#go`` / ``K.go`` -> ``go``."""
    name = str(method.name)
    for sep in ("#", "."):
        if sep in name:
            return name.rsplit(sep, 1)[1]
    return name


#: The scopes whose superclass a ``super`` inside them reaches. A
#: ``singleton_class`` (``class << self``) is walked past to its class.
_SUPER_OWNER_NODES: frozenset[str] = frozenset({"class", "module"})


def _superclass_of(
    class_sym: Optional[Symbol],
    superclass_by_qualified: dict[str, str],
) -> Optional[str]:
    """The superclass of the class ``class_sym`` declares.

    ``class_sym`` is the class whose declaration contains the ``super``,
    found by POSITION, not by its bare name in the run-wide registry: the
    bare name drops the module nesting, and ``module A; class Foo < BaseA``
    and ``module B; class Foo < BaseB`` are two classes whose ``super`` calls
    would both read whichever ``Foo`` registered last (WI-gikuk).

    Ruby REOPENS a class by its full constant path, so a ``class Foo`` that
    declares no superclass (``class Foo`` in another file, adding methods) is
    the same class as the ``class Foo < Base`` elsewhere. Its superclass is
    looked up by the QUALIFIED name (``A::Foo``) in ``superclass_by_qualified``,
    never by the bare one. A module has no superclass.
    """
    if class_sym is None or class_sym.kind != "class":
        return None
    bases = (class_sym.meta or {}).get("base_classes")
    if bases:
        return str(bases[0])
    return superclass_by_qualified.get(class_sym.qualified_name or "")


def superclasses_by_qualified_name(symbols: Iterable[Symbol]) -> dict[str, str]:
    """``{qualified class name: declared superclass}`` over ``symbols``.

    The first declaration that names a superclass wins (Ruby raises
    ``superclass mismatch`` on a second, different one).
    """
    out: dict[str, str] = {}
    for sym in symbols:
        bases = (sym.meta or {}).get("base_classes") if sym.kind == "class" else None
        if bases and sym.qualified_name:
            out.setdefault(sym.qualified_name, str(bases[0]))
    return out


def _ruby_stub(
    caller: Symbol, callee_name: str, line: int, run_id: str, **facts: object,
) -> Edge:
    """An unresolved call of ``callee_name`` from ``caller`` with the call's
    facts (ADR-0064), marked ``stdlib_member_name`` when Ruby's core or Rails
    defines the name (:data:`_RUBY_LIBRARY_METHOD_NAMES`): the single-holder
    name linker then does not bind it to the repository's one ``m``."""
    return make_unresolved_edge(
        lang="ruby",
        src_id=caller.id,
        callee_name=callee_name,
        line=line,
        pass_id=PASS_ID,
        run_id=run_id,
        stdlib_member_name=callee_name in _RUBY_LIBRARY_METHOD_NAMES,
        **facts,  # type: ignore[arg-type]
    )


def _super_edge(
    current_method: Symbol,
    class_sym: Optional[Symbol],
    superclass_by_qualified: dict[str, str],
    line: int,
    run_id: str,
) -> Edge:
    """``super`` / ``super(...)``: a call to the enclosing method's name, from
    the PARENT of the class whose declaration holds it (WI-misir), reported
    with that class as ``enclosing_class`` and ``super_receiver`` (ADR-0064):
    the inherited-calls linker starts its walk at the class's parents (its
    superclass and the modules it includes), so the class's own method of
    that name, the usual caller, is never the callee. A ``super`` in a
    module's method reaches the next ancestor of whatever class includes the
    module, which no declaration says: it carries no class. The module slot
    keeps the superclass as written, never ``external``: the method is the
    project's (or its gem parent's), and ``external`` would let a Kernel
    catalogue row of the same short name match it."""
    parent = _superclass_of(class_sym, superclass_by_qualified)
    own = class_sym.name if class_sym is not None and class_sym.kind == "class" else None
    return _ruby_stub(
        current_method, _method_short_name(current_method), line, run_id,
        module_hint=parent or "external",
        enclosing_class=own,
        super_receiver=own is not None,
        call_construct="method",
    )


def _caller_owner(caller: Symbol) -> tuple[Optional[str], Optional[str]]:
    """The class or module a send to ``self`` from ``caller`` starts at, as
    ``(lexical path, Symbol.name)``: the method's owner, or the class or
    module itself for a call in its body (INV-bamij); ``(None, None)`` for a
    top-level method and the file anchor. The path keys the file's table
    (:func:`_registry_key`); the name is what the inherited-calls linker
    matches ``enclosing_class`` against."""
    if caller.kind in ("class", "module"):
        return _registry_key(caller), caller.name
    owner = _owner_path(caller)
    if owner is None:
        return None, None
    # A method with an owner is named ``X#go`` (instance) or ``X.run`` (class
    # or module method): the owner as written is what precedes the separator.
    name = caller.name
    return owner, name.split("#", 1)[0] if "#" in name else name.split(".", 1)[0]


def _bare_send(
    callee_name: str,
    caller: Symbol,
    local_symbols: dict[str, Symbol],
    line: int,
    edges: list[Edge],
    run_id: str,
    bare: bool,
    shared_keys: frozenset[str] = frozenset(),
) -> None:
    """A send to ``self``: ``self.m(...)``, a receiverless ``m(x)`` and a bare
    ``m`` that is not a local (``bare``; ``meta["receiver"]`` is ``"bare"``
    for it, ``"self"`` for the other two).

    Bound only on the evidence Ruby's method lookup starts with, in THIS
    file. From a method of a class or module (or its body): the class's own
    method, found by the owner's full lexical path (``own_type``). From a
    top-level method or the file, whose ``self`` is ``main``: the top-level
    ``def`` of this file, a private method of ``Object`` (``same_file``),
    unless another file defines one too (``shared_keys``: the one loaded
    last is meant, which the source does not say). Anything else is reported
    (ADR-0064): with the class as ``enclosing_class``, so the inherited-calls
    linker walks the class's declarations in other files and its ancestors;
    with nothing at all from the top level. No lookup of the name among the
    repository's methods is made: the same name on another class is not
    this call's callee, and a top-level ``def`` called from a class is one
    only if nothing in the class's ancestors (which may be a gem's) defines
    it. The module slot names the class, unless the name is one Kernel
    defines (:data:`_RUBY_KERNEL_NAMES`), whose ``external`` slot lets the
    I/O catalogue's Kernel rows (``system``, ``open``) reach it."""
    owner, enclosing = _caller_owner(caller)
    found = (
        [local_symbols.get(f"{owner}{sep}{callee_name}") for sep in ("#", ".")]
        if owner is not None
        else [None if callee_name in shared_keys else local_symbols.get(callee_name)]
    )
    callee = next((c for c in found if c is not None and c.kind == "method"), None)
    if callee is not None and (owner is not None or _owner_path(callee) is None):
        # ``callee == caller`` is direct recursion, a real call (python and
        # java emit it; WI-misir).
        edges.append(Edge.create(
            src=caller.id,
            dst=callee.id,
            edge_type="calls",
            line=line,
            evidence_type="ast_call",
            resolution_score=_RUBY_SAME_CLASS_GRADE,
            origin=PASS_ID,
            origin_run_id=run_id,
            meta={"call_construct": "method", "receiver": "bare" if bare else "self"},
            resolved_by=resolved_by.OWN_TYPE if owner is not None else resolved_by.SAME_FILE,
        ))
        return
    kernel = enclosing is None or callee_name in _RUBY_KERNEL_NAMES
    edges.append(_ruby_stub(
        caller, callee_name, line, run_id,
        module_hint="external" if kernel else enclosing,
        enclosing_class=enclosing,
        call_construct=None if kernel else "method",
    ))


def _receiver_is_self(call: "tree_sitter.Node", source: bytes) -> bool:
    """Whether ``call`` has no receiver or the receiver ``self``."""
    receiver = call.child_by_field_name("receiver")
    return receiver is None or node_text(receiver, source) == "self"


def _extract_ruby_signature(
    node: "tree_sitter.Node", source: bytes
) -> Optional[str]:
    """Extract method signature from a method node.

    Returns signature in format: (param, param2 = ..., keyword:, &block)
    Ruby is dynamically typed, so no type annotations are included.
    """
    params: list[str] = []

    # Find parameters node
    params_node = _find_child_by_field(node, "parameters")
    if params_node is None:
        return "()"

    for child in params_node.children:
        if child.type == "identifier":
            # Simple positional parameter
            params.append(node_text(child, source))
        elif child.type == "optional_parameter":
            # Parameter with default value: name = value
            name_node = find_child_by_type(child, "identifier")
            if name_node:
                param_name = node_text(name_node, source)
                params.append(f"{param_name} = ...")
        elif child.type == "keyword_parameter":
            # Keyword parameter: name: or name: value
            name_node = find_child_by_type(child, "identifier")
            if name_node:
                param_name = node_text(name_node, source)
                # Check if it has a default value (look for value node after identifier)
                has_value = False
                for pc in child.children:
                    # Skip the identifier and punctuation - look for an actual value
                    if pc.type not in ("identifier", ":"):
                        has_value = True
                        break
                if has_value:
                    params.append(f"{param_name}: ...")
                else:
                    params.append(f"{param_name}:")
        elif child.type == "splat_parameter":
            # *args
            name_node = find_child_by_type(child, "identifier")
            if name_node:
                param_name = node_text(name_node, source)
                params.append(f"*{param_name}")
            else:
                params.append("*")  # pragma: no cover - bare splat
        elif child.type == "hash_splat_parameter":
            # **kwargs
            name_node = find_child_by_type(child, "identifier")
            if name_node:
                param_name = node_text(name_node, source)
                params.append(f"**{param_name}")
            else:
                params.append("**")  # pragma: no cover - bare hash splat
        elif child.type == "block_parameter":
            # &block
            name_node = find_child_by_type(child, "identifier")
            if name_node:
                param_name = node_text(name_node, source)
                params.append(f"&{param_name}")

    params_str = ", ".join(params)
    return f"({params_str})"


@dataclass
class _NamespaceBlock:
    """A Rails namespace or scope-module block with byte range and prefix."""

    start_byte: int
    end_byte: int
    prefix: str
    prefixes_path: bool  # True for namespace (prefixes URL), False for scope module:


@dataclass
class _ResourceBlock:
    """A Rails resources/resource block with byte range and resource info.

    Tracks do_block byte ranges so nested routes, member blocks, and
    collection blocks can determine their parent resource context.
    """

    start_byte: int
    end_byte: int
    resource_name: str  # e.g. "users", "posts"
    path_prefix: str  # e.g. "/users/:user_id" for nested resources
    controller_name: str  # e.g. "users", "admin/users"
    is_singular: bool  # True for 'resource', False for 'resources'


@dataclass
class _MemberCollectionBlock:
    """A Rails member/collection block inside a resource.

    member do ... end → routes at /:id/action
    collection do ... end → routes at /action
    """

    start_byte: int
    end_byte: int
    block_type: str  # "member" or "collection"
    resource_block: _ResourceBlock  # parent resource


def _find_namespace_blocks(
    root: "tree_sitter.Node",
    source: bytes,
) -> list[_NamespaceBlock]:
    """Pre-scan tree for Rails namespace and scope-module blocks.

    Finds ``namespace :name do ... end`` and ``scope module: :name do ... end``
    call nodes and records their byte ranges and prefix strings. Supports
    arbitrarily deep nesting by recursive descent.

    Returns:
        Flat list of _NamespaceBlock entries. Nesting is determined by
        containment of byte ranges at query time.
    """
    blocks: list[_NamespaceBlock] = []

    for n in iter_tree(root):
        if n.type != "call":
            continue

        method_node = None
        for child in n.children:
            if child.type == "identifier":
                method_node = child
                break
        if method_node is None:  # pragma: no cover
            continue

        method_name = node_text(method_node, source).lower()

        if method_name == "namespace":
            # namespace :admin do ... end
            args_node = _find_child_by_field(n, "arguments")
            if not args_node:  # pragma: no cover - namespace always has arguments
                continue
            for arg in args_node.children:
                if arg.type == "simple_symbol":
                    ns_name = node_text(arg, source).strip(":")
                    # Find the do_block to get the byte range
                    for child in n.children:
                        if child.type in ("do_block", "block"):
                            blocks.append(_NamespaceBlock(
                                start_byte=child.start_byte,
                                end_byte=child.end_byte,
                                prefix=ns_name,
                                prefixes_path=True,
                            ))
                    break

        elif method_name == "scope":
            # scope module: :admin do ... end
            args_node = _find_child_by_field(n, "arguments")
            if not args_node:  # pragma: no cover - scope always has arguments
                continue
            for arg in args_node.children:
                if arg.type == "pair":
                    # Look for module: :name pattern
                    # pair children: hash_key_symbol("module"), :, simple_symbol(":admin")
                    pair_children = list(arg.children)
                    has_module_key = any(
                        c.type == "hash_key_symbol"
                        and node_text(c, source).strip(":") == "module"
                        for c in pair_children
                    )
                    if not has_module_key:
                        continue
                    # Find the value (simple_symbol after the key)
                    val_node = None
                    for c in pair_children:
                        if c.type == "simple_symbol":
                            text = node_text(c, source).strip(":")
                            if text != "module":
                                val_node = c
                                break
                    if val_node is not None:
                        mod_name = node_text(val_node, source).strip(":")
                        for child in n.children:
                            if child.type in ("do_block", "block"):
                                blocks.append(_NamespaceBlock(
                                    start_byte=child.start_byte,
                                    end_byte=child.end_byte,
                                    prefix=mod_name,
                                    prefixes_path=False,
                                ))
                        break

    return blocks


def _get_namespace_context(
    node: "tree_sitter.Node",
    ns_blocks: list[_NamespaceBlock],
) -> tuple[str, str]:
    """Determine the namespace prefix for a route call node.

    Checks which namespace blocks contain this node (by byte range) and
    accumulates their prefixes. Returns separate path_prefix and
    module_prefix since ``scope module:`` only affects the controller
    lookup, not the URL path.

    Returns:
        (path_prefix, module_prefix) where each is a slash-separated
        prefix string like "admin" or "api/v1", or "" if no namespace.
    """
    path_parts: list[str] = []
    module_parts: list[str] = []

    # Sort by start_byte to get outermost-first ordering
    containing = [
        b for b in ns_blocks
        if b.start_byte <= node.start_byte and node.end_byte <= b.end_byte
    ]
    containing.sort(key=lambda b: b.start_byte)

    for block in containing:
        module_parts.append(block.prefix)
        if block.prefixes_path:
            path_parts.append(block.prefix)

    return "/".join(path_parts), "/".join(module_parts)


def _find_resource_blocks(
    root: "tree_sitter.Node",
    source: bytes,
    ns_blocks: list[_NamespaceBlock],
) -> tuple[list[_ResourceBlock], list[_MemberCollectionBlock]]:
    """Pre-scan tree for Rails resource blocks with do_block children.

    Finds ``resources :name do ... end`` and ``resource :name do ... end``
    calls that contain nested route definitions. Also finds ``member do``
    and ``collection do`` blocks inside resource blocks.

    Returns:
        Tuple of (resource_blocks, member_collection_blocks).
    """
    resource_blocks: list[_ResourceBlock] = []
    mc_blocks: list[_MemberCollectionBlock] = []

    for n in iter_tree(root):
        if n.type != "call":
            continue

        method_node = None
        for child in n.children:
            if child.type == "identifier":
                method_node = child
                break
        if method_node is None:  # pragma: no cover
            continue

        method_name = node_text(method_node, source).lower()

        if method_name in ("resources", "resource"):
            # Check for do_block
            do_block = None
            for child in n.children:
                if child.type in ("do_block", "block"):
                    do_block = child
                    break
            if do_block is None:
                continue

            # Extract resource name from arguments
            args_node = _find_child_by_field(n, "arguments")
            if not args_node:  # pragma: no cover
                continue
            resource_name = None
            for arg in args_node.children:
                if arg.type == "simple_symbol":
                    resource_name = node_text(arg, source).strip(":")
                    break
            if resource_name is None:  # pragma: no cover
                continue

            # Get namespace context for this resource
            ns_path_prefix, module_prefix = _get_namespace_context(n, ns_blocks)

            # Build path prefix: check if this resource is nested inside
            # another resource block. Use innermost (narrowest byte range).
            parent_path = ""
            innermost_rb = None
            for rb in resource_blocks:
                if rb.start_byte <= n.start_byte and n.end_byte <= rb.end_byte:
                    if innermost_rb is None or rb.start_byte > innermost_rb.start_byte:
                        innermost_rb = rb
            if innermost_rb is not None:
                parent_path = innermost_rb.path_prefix

            is_singular = method_name == "resource"
            if innermost_rb is not None:
                # Nested resource: include parent's :parent_id in path
                parent_id = f":{_singularize(innermost_rb.resource_name)}_id"
                path_prefix = f"{parent_path}/{parent_id}/{resource_name}"
            elif ns_path_prefix:
                path_prefix = f"/{ns_path_prefix}/{resource_name}"
            else:
                path_prefix = f"/{resource_name}"

            controller_name = (
                f"{module_prefix}/{resource_name}" if module_prefix else resource_name
            )

            resource_blocks.append(_ResourceBlock(
                start_byte=do_block.start_byte,
                end_byte=do_block.end_byte,
                resource_name=resource_name,
                path_prefix=path_prefix,
                controller_name=controller_name,
                is_singular=is_singular,
            ))

        elif method_name in ("member", "collection"):
            # member/collection blocks inside a resource
            do_block = None
            for child in n.children:
                if child.type in ("do_block", "block"):
                    do_block = child
                    break
            if do_block is None:  # pragma: no cover - member/collection always has block
                continue

            # Find the innermost enclosing resource block
            parent_rb = None
            for rb in resource_blocks:
                if rb.start_byte <= n.start_byte and n.end_byte <= rb.end_byte:
                    if parent_rb is None or rb.start_byte > parent_rb.start_byte:
                        parent_rb = rb
            if parent_rb is None:  # pragma: no cover - member/collection always inside resource
                continue

            mc_blocks.append(_MemberCollectionBlock(
                start_byte=do_block.start_byte,
                end_byte=do_block.end_byte,
                block_type=method_name,
                resource_block=parent_rb,
            ))

    return resource_blocks, mc_blocks


def _get_resource_context(
    node: "tree_sitter.Node",
    resource_blocks: list[_ResourceBlock],
    mc_blocks: list[_MemberCollectionBlock],
) -> tuple[str, str, str]:
    """Determine the resource context for a route call node.

    Checks if the node is inside a resource block's do_block (nested resource)
    or inside a member/collection block.

    Returns:
        (path_prefix, controller_name, context_type) where context_type is
        "nested", "member", "collection", or "" if no resource context.
    """
    # Check member/collection blocks first (more specific, innermost wins)
    innermost_mc = None
    for mc in mc_blocks:
        if mc.start_byte <= node.start_byte and node.end_byte <= mc.end_byte:
            if innermost_mc is None or mc.start_byte > innermost_mc.start_byte:
                innermost_mc = mc
    if innermost_mc is not None:
        return (
            innermost_mc.resource_block.path_prefix,
            innermost_mc.resource_block.controller_name,
            innermost_mc.block_type,
        )

    # Check resource blocks (for nested resources, innermost wins)
    innermost_rb = None
    for rb in resource_blocks:
        if rb.start_byte <= node.start_byte and node.end_byte <= rb.end_byte:
            if innermost_rb is None or rb.start_byte > innermost_rb.start_byte:
                innermost_rb = rb
    if innermost_rb is not None:
        parent_id = f":{_singularize(innermost_rb.resource_name)}_id"
        return f"{innermost_rb.path_prefix}/{parent_id}", "", "nested"

    return "", "", ""


def _singularize(name: str) -> str:
    """Naive English singularization for Rails resource param names.

    Converts plural resource names to singular for :resource_id params.
    E.g., "users" → "user", "posts" → "post", "categories" → "category".
    """
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith("ses") or name.endswith("xes") or name.endswith("zes"):
        return name[:-2]
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name


def _extract_on_keyword(args_node: "tree_sitter.Node", source: bytes) -> str | None:
    """Extract the value of an ``on:`` keyword argument from a call's args.

    Rails inline member/collection routes use ``on: :member`` or
    ``on: :collection`` as keyword arguments instead of ``member do...end``
    blocks.  E.g.::

        get :setup, on: :member
        match :verify, on: :member, via: [:get, :post]

    Returns "member", "collection", or None if no ``on:`` keyword found.
    """
    for arg in args_node.children:
        if arg.type == "pair":
            # pair children: hash_key_symbol("on"), ":"(punctuation), simple_symbol(":member")
            children = list(arg.children)
            if len(children) >= 3:
                key_child = children[0]
                val_child = children[-1]
                if key_child.type == "hash_key_symbol":
                    key_text = node_text(key_child, source).strip(":")
                    if key_text == "on" and val_child.type == "simple_symbol":
                        return node_text(val_child, source).strip(":")
    return None


def _extract_resource_action_filter(
    args_node: "tree_sitter.Node",
    source: bytes,
) -> tuple[str | None, set[str]]:
    """Extract ``only:`` or ``except:`` action filter from resource call args.

    Rails resources support filtering generated routes::

        resources :users, except: [:destroy]
        resources :posts, only: [:index, :show]

    Returns (filter_type, actions) where filter_type is "only", "except",
    or None if no filter found.  Actions is a set of action name strings.
    """
    for arg in args_node.children:
        if arg.type == "pair":
            children = list(arg.children)
            if len(children) < 3:  # pragma: no cover
                continue
            key_child = children[0]
            val_child = children[-1]
            if key_child.type != "hash_key_symbol":  # pragma: no cover
                continue
            key_text = node_text(key_child, source).strip(":")
            if key_text not in ("only", "except"):
                continue  # Skip other kwargs like path:, controller:, etc.
            actions: set[str] = set()
            if val_child.type == "array":
                for el in val_child.children:
                    if el.type == "simple_symbol":
                        actions.add(node_text(el, source).strip(":"))
            elif val_child.type == "simple_symbol":
                # Single action: only: :index
                actions.add(node_text(val_child, source).strip(":"))
            if actions:
                return key_text, actions
    return None, set()


def _extract_rails_routes(
    node: "tree_sitter.Node",
    source: bytes,
    file_path: Path,
    symbol_by_name: dict[str, Symbol],
    run_id: str,
) -> tuple[list[UsageContext], list[Symbol]]:
    """Extract UsageContext records AND Symbol objects for Rails/Sinatra route DSL calls.

    Detects patterns like:
    - Rails: get '/users', to: 'users#index'
    - Rails: post '/login', to: 'sessions#create'
    - Rails: resources :users, resource :profile
    - Rails: namespace :admin do resources :users end
    - Rails: scope module: :admin do resources :settings end
    - Rails: resources :users do resources :posts end (nested)
    - Rails: resources :users do member do post :activate end end
    - Rails: resources :users do collection do get :active end end
    - Sinatra: get '/path' do ... end
    - Sinatra: post '/users' do ... end

    Namespace DSL blocks (``namespace :admin do ... end``) prepend a module
    path to controller_action and URL path. ``scope module: :name`` only
    prefixes the controller lookup, not the URL path.

    Nested resources prepend parent path + :parent_id to child routes.
    Member blocks generate routes at /:id/action. Collection blocks
    generate routes at /action.

    The has_block metadata field distinguishes Sinatra (with block) from Rails (with to: option).

    Returns:
        Tuple of (UsageContext list, Symbol list) for YAML pattern matching.
        Symbols are route markers carrying meta.framework_role="route".
    """
    contexts: list[UsageContext] = []
    route_symbols: list[Symbol] = []

    # Pre-scan for namespace/scope-module blocks
    ns_blocks = _find_namespace_blocks(node, source)

    # Pre-scan for resource blocks and member/collection blocks
    resource_blocks, mc_blocks = _find_resource_blocks(node, source, ns_blocks)

    for n in iter_tree(node):
        if n.type != "call":
            continue

        # Get the method name
        method_node = None
        for child in n.children:
            if child.type == "identifier":
                method_node = child
                break

        if method_node is None:  # pragma: no cover
            continue

        method_name = node_text(method_node, source).lower()

        # Check if it's an HTTP method route or resources
        if method_name not in HTTP_METHODS and method_name not in ("resources", "resource"):
            continue

        # Check resource context (nested resources, member/collection blocks)
        res_path_prefix, res_controller, res_context = _get_resource_context(
            n, resource_blocks, mc_blocks
        )

        # Extract route path from first argument
        args_node = _find_child_by_field(n, "arguments")
        if not args_node:  # pragma: no cover
            continue

        route_path = None
        controller_action = None
        resource_name = None  # Original resource name before namespace prefix
        action_name = None  # For member/collection routes

        for arg in args_node.children:
            # String path for HTTP method routes
            if arg.type == "string" and method_name in HTTP_METHODS:
                content_node = find_child_by_type(arg, "string_content")
                if content_node:
                    route_path = node_text(content_node, source)
                    break
            # Symbol for resources/resource
            elif arg.type == "simple_symbol" and method_name in ("resources", "resource"):
                resource_name = node_text(arg, source).strip(":")
                route_path = resource_name
                break
            # Symbol for HTTP method inside member/collection block
            # e.g., member do; post :activate; end → action_name = "activate"
            # Also handles inline on: :member / on: :collection keyword args:
            # e.g., get :setup, on: :member → GET /resources/:id/setup
            elif (
                arg.type == "simple_symbol"
                and method_name in HTTP_METHODS
                and res_context in ("member", "collection", "nested")
            ):
                action_name = node_text(arg, source).strip(":")
                # Determine effective context: use inline on: keyword if present
                effective_context = res_context
                mc_path_prefix = res_path_prefix
                mc_controller = res_controller
                if res_context == "nested":
                    # Check for on: :member or on: :collection keyword arg
                    on_value = _extract_on_keyword(args_node, source)
                    if on_value in ("member", "collection"):
                        effective_context = on_value
                        # For inline on: keyword, use the enclosing resource
                        # block's own prefix (not the nested prefix which
                        # includes :parent_id)
                        innermost_rb = None
                        for rb in resource_blocks:
                            if rb.start_byte <= n.start_byte and n.end_byte <= rb.end_byte:
                                if innermost_rb is None or rb.start_byte > innermost_rb.start_byte:
                                    innermost_rb = rb
                        if innermost_rb is not None:
                            mc_path_prefix = innermost_rb.path_prefix
                            mc_controller = innermost_rb.controller_name
                    else:
                        # Not a member/collection route — this is just a
                        # symbol arg inside a nested resource, skip
                        continue
                if effective_context == "member":
                    route_path = f"{mc_path_prefix}/:id/{action_name}"
                else:  # collection
                    route_path = f"{mc_path_prefix}/{action_name}"
                controller_action = f"{mc_controller}#{action_name}"
                break
            # Handle "path" => "controller#action" syntax (pair with string key)
            elif arg.type == "pair" and method_name in HTTP_METHODS:
                # Extract key (path) and value (controller#action) from pair
                pair_children = list(arg.children)
                if len(pair_children) >= 2:
                    key_node = pair_children[0]
                    val_node = pair_children[-1]  # Last child is value
                    if key_node.type == "string":
                        key_content = find_child_by_type(key_node, "string_content")
                        if key_content:
                            route_path = node_text(key_content, source)
                    if val_node.type == "string":
                        val_content = find_child_by_type(val_node, "string_content")
                        if val_content:
                            controller_action = node_text(val_content, source)
                if route_path:
                    break

        if not route_path:  # pragma: no cover
            continue

        # Try to extract controller#action from 'to:' option (e.g., match "path", to: "ctrl#act")
        for arg in args_node.children:
            if arg.type == "pair":
                for pair_child in arg.children:
                    if pair_child.type in ("hash_key_symbol", "simple_symbol"):
                        key_text = node_text(pair_child, source).strip(":")
                        if key_text == "to":
                            for sibling in arg.children:
                                if sibling.type == "string":
                                    content = find_child_by_type(sibling, "string_content")
                                    if content:
                                        controller_action = node_text(content, source)

        # Check for block (Sinatra style: get '/path' do ... end)
        has_block = False
        for child in n.children:
            if child.type in ("do_block", "block"):
                has_block = True
                break

        # Apply namespace prefix (e.g., namespace :admin do resources :users end)
        path_prefix, module_prefix = _get_namespace_context(n, ns_blocks)

        # For nested resources and member/collection routes, the resource
        # block's path_prefix already includes the namespace path. Skip
        # namespace path prefixing to avoid double-prefixing.
        if res_context in ("nested", "member", "collection"):
            # Resource nesting prefix already includes namespace path
            if res_context == "nested" and method_name in ("resources", "resource"):
                if route_path.startswith("/"):  # pragma: no cover - resource symbols don't start with /
                    route_path = f"{res_path_prefix}{route_path}"
                else:
                    route_path = f"{res_path_prefix}/{route_path}"
            # member/collection paths were already built in argument extraction
        else:
            # Standard namespace prefix (not inside a resource block)
            if path_prefix:
                if route_path.startswith("/"):
                    route_path = f"/{path_prefix}{route_path}"
                else:
                    route_path = f"{path_prefix}/{route_path}"

        # Prefix the controller_action for both namespace and scope module:
        if module_prefix and controller_action:
            controller_action = f"{module_prefix}/{controller_action}"

        # Build metadata
        http_method = method_name.upper() if method_name in HTTP_METHODS else "RESOURCES"
        metadata: dict[str, str | bool] = {
            "route_path": route_path,
            "http_method": http_method,
            "has_block": has_block,  # True for Sinatra-style, False for Rails-style
        }
        if controller_action:
            metadata["controller_action"] = controller_action
        elif method_name in ("resources", "resource") and resource_name is not None:
            # For resources :users, infer controller_action from resource name
            # Rails convention: resources :users → UsersController#index (primary entry)
            # This enables route-handler linking for resource routes
            if module_prefix:
                metadata["controller_action"] = f"{module_prefix}/{resource_name}#index"
            else:
                metadata["controller_action"] = f"{resource_name}#index"

        # Create span
        span = Span(
            start_line=n.start_point[0] + 1,
            end_line=n.end_point[0] + 1,
            start_col=n.start_point[1],
            end_col=n.end_point[1],
        )

        # Create UsageContext (for backwards compatibility)
        ctx = UsageContext.create(
            kind="call",
            context_name=method_name,  # e.g., "get", "post", "resources"
            position="args[0]",
            path=str(file_path),
            span=span,
            symbol_ref=None,  # Route DSL doesn't reference a handler symbol directly
            metadata=metadata,
        )
        contexts.append(ctx)

        # Create route-marker Symbol(s) (meta.framework_role="route")
        # This enables route detection and entrypoint detection for Rails apps
        normalized_path = route_path if route_path.startswith("/") else f"/{route_path}"

        # Extract only:/except: action filter for resources/resource
        filter_type, filter_actions = _extract_resource_action_filter(
            args_node, source,
        ) if method_name in ("resources", "resource") else (None, set())

        # For resources/resource, expand into all RESTful routes
        # This enables route-handler linking for all controller actions
        if method_name == "resources":
            # resources :users creates 7 RESTful routes
            restful_routes = [
                ("GET", normalized_path, "index"),  # GET /users
                ("GET", f"{normalized_path}/new", "new"),  # GET /users/new
                ("POST", normalized_path, "create"),  # POST /users
                ("GET", f"{normalized_path}/:id", "show"),  # GET /users/:id
                ("GET", f"{normalized_path}/:id/edit", "edit"),  # GET /users/:id/edit
                ("PATCH", f"{normalized_path}/:id", "update"),  # PATCH /users/:id
                ("DELETE", f"{normalized_path}/:id", "destroy"),  # DELETE /users/:id
            ]
            # Apply only:/except: filter
            if filter_type == "only":
                restful_routes = [r for r in restful_routes if r[2] in filter_actions]
            elif filter_type == "except":
                restful_routes = [r for r in restful_routes if r[2] not in filter_actions]
            # Controller name from resource with namespace prefix
            # e.g., namespace :admin do resources :users end → admin/users
            assert resource_name is not None
            controller_name = (
                f"{module_prefix}/{resource_name}" if module_prefix else resource_name
            )
            for http_meth, route_pth, action in restful_routes:
                route_symbols.append(make_route_symbol(
                    language="ruby",
                    path=str(file_path),
                    span=span,
                    method=http_meth,
                    route_path=route_pth,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    extra_meta={
                        "controller_action": f"{controller_name}#{action}",
                    },
                    is_exported=True,
                ))
        elif method_name == "resource":
            # resource :profile creates 6 RESTful routes (no index)
            # Singular resource uses singular path but plural controller
            restful_routes = [
                ("GET", normalized_path, "show"),  # GET /profile
                ("GET", f"{normalized_path}/new", "new"),  # GET /profile/new
                ("POST", normalized_path, "create"),  # POST /profile
                ("GET", f"{normalized_path}/edit", "edit"),  # GET /profile/edit
                ("PATCH", normalized_path, "update"),  # PATCH /profile
                ("DELETE", normalized_path, "destroy"),  # DELETE /profile
            ]
            # Apply only:/except: filter
            if filter_type == "only":
                restful_routes = [r for r in restful_routes if r[2] in filter_actions]
            elif filter_type == "except":
                restful_routes = [r for r in restful_routes if r[2] not in filter_actions]
            # Rails convention: resource :profile → ProfilesController (pluralized)
            # Don't double the 's' if name already ends in 's' (audit_logs, settings)
            assert resource_name is not None
            base_name = resource_name if resource_name.endswith("s") else f"{resource_name}s"
            controller_name = (
                f"{module_prefix}/{base_name}" if module_prefix else base_name
            )
            for http_meth, route_pth, action in restful_routes:
                route_symbols.append(make_route_symbol(
                    language="ruby",
                    path=str(file_path),
                    span=span,
                    method=http_meth,
                    route_path=route_pth,
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    extra_meta={
                        "controller_action": f"{controller_name}#{action}",
                    },
                    is_exported=True,
                ))
        else:
            # Regular HTTP method route (get, post, etc.)
            route_meta: dict[str, str | bool] = {"has_block": has_block}
            if controller_action:
                route_meta["controller_action"] = controller_action
            route_symbols.append(make_route_symbol(
                language="ruby",
                path=str(file_path),
                span=span,
                method=http_method,
                route_path=normalized_path,
                origin=PASS_ID,
                origin_run_id=run_id,
                extra_meta=route_meta,
                is_exported=True,
            ))

    return contexts, route_symbols


def _extract_block_callback_edges(
    block_node: "tree_sitter.Node",
    source: bytes,
    class_path: str,
    class_sym: "Symbol",
    file_symbols: dict[str, "Symbol"],
    global_symbols: dict[str, "Symbol"],
    line: int,
    run_id: str,
    edges: list["Edge"],
) -> None:
    """Extract method calls from a block-style callback body.

    Block-style callbacks like ``after_commit do ... end`` or
    ``before_save { ... }`` contain inline code rather than symbol
    references. We walk the block body looking for bare method calls
    (identifiers that resolve to methods in the enclosing class) and
    create ``dispatches_to`` edges for each.

    Args:
        block_node: The ``do_block`` or ``block`` AST node.
        source: Raw source bytes.
        class_path: Lexical path of the class containing the callback.
        class_sym: The Symbol for the enclosing class.
        file_symbols: File-local symbol lookup dict.
        global_symbols: Global symbol lookup dict.
        line: Source line of the callback declaration (for edge metadata).
        run: Current AnalysisRun for edge provenance.
        edges: List to append new edges to (mutated in place).
    """
    for child in iter_tree(block_node):
        # Look for bare method calls: identifiers used as call targets
        if child.type == "call":
            callee_node = child.child_by_field_name("method")
            if callee_node is None:
                continue  # pragma: no cover
            call_name = node_text(callee_node, source)
        elif child.type == "identifier":
            # Bare identifiers at statement level may be method calls
            # (Ruby allows calling methods without parens)
            # Only consider identifiers that are direct children of
            # body_statement / block_body (statement-level)
            if child.parent is None or child.parent.type not in (
                "body_statement", "block_body",
            ):
                continue  # pragma: no cover — identifiers nested in expressions
            call_name = node_text(child, source)
        else:
            continue

        # Resolve to a method in the enclosing class
        qualified = f"{class_path}#{call_name}"
        callee = file_symbols.get(qualified) or global_symbols.get(qualified)
        if callee is None:
            continue

        edges.append(Edge.create(
            src=class_sym.id,
            dst=callee.id,
            edge_type="dispatches_to",
            line=line,
            evidence_type="ast_call_direct",
            origin=PASS_ID,
            origin_run_id=run_id,
            meta={"framework_dispatch": "rails_block_callback", "mechanism": "callback"},
        ))


def _extract_rails_callbacks(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    file_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    resolver: NameResolver,
    run_id: str,
) -> list[Edge]:
    """Extract Rails callback ``dispatches_to`` edges from callback declarations.

    Rails controllers and models declare callbacks at class level in two forms:

    **Named-method callbacks** (symbol arguments):
        before_action :authenticate!
        after_action :log_request, :track_metrics
        before_save :normalize_email

    **Block-style callbacks** (do...end or braces):
        after_commit do
          provision_database
        end
        before_save { normalize_name }

    Named-method callbacks create edges from the class to the named method
    (``meta["framework_dispatch"] = "rails_callback"``). Block-style callbacks
    extract method calls from the block body and create edges for each
    (``meta["framework_dispatch"] = "rails_block_callback"``). Both forms emit
    ``dispatches_to`` with evidence ``ast_call_direct`` and
    ``meta["mechanism"] = "callback"``.

    Also handles legacy Rails 3 API: before_filter, after_filter, around_filter.
    """
    _caller_path = str(file_path)
    edges: list[Edge] = []

    for node in iter_tree(tree.root_node):
        if node.type != "call":
            continue

        # Get method name via the 'method' field
        method_node = node.child_by_field_name("method")
        if method_node is None:  # pragma: no cover
            continue

        method_name = node_text(method_node, source)
        if method_name not in RAILS_CALLBACK_METHODS:
            continue

        # Must be at class body level (not inside a method)
        enclosing_class_name, enclosing_type = _get_enclosing_class_or_module(node, source)
        if enclosing_type != "class" or enclosing_class_name is None:
            continue

        # Must not be inside a method (class-level declaration)
        current = node.parent
        inside_method = False
        while current is not None:
            if current.type == "method":
                inside_method = True
                break
            if current.type in ("class", "module"):
                break
            current = current.parent
        if inside_method:
            continue  # pragma: no cover — unusual but defensive

        # Find the class symbol, by the class's full lexical path: the name
        # alone is shared by ``Admin::UsersController`` and
        # ``Api::UsersController`` (INV-dosoz).
        class_path = _lexical_nesting(node, source)[0]
        class_sym = file_symbols.get(class_path)
        if class_sym is None:  # pragma: no cover — class always in file symbols
            continue

        # Extract callback method names from symbol arguments
        args_node = _find_child_by_field(node, "arguments")
        if args_node is not None:
            for arg in args_node.children:
                if arg.type != "simple_symbol":
                    continue

                callback_name = node_text(arg, source).lstrip(":")

                # Resolve the callback method:
                # 1. Try qualified name in same class (ClassName#callback)
                qualified = f"{class_path}#{callback_name}"
                callee = file_symbols.get(qualified) or global_symbols.get(qualified)

                # 2. Try bare name in file-local symbols
                if callee is None:
                    callee = file_symbols.get(callback_name)

                # 3. Try bare name in global symbols (parent class)
                if callee is None:
                    callee = global_symbols.get(callback_name)

                # 4. Try resolver for fuzzy matching (steps 1-3 cover all normal cases;
                #    this handles edge cases where method name differs from symbol key)
                if callee is None:  # pragma: no cover — steps 1-3 resolve all known patterns
                    lookup = resolver.lookup(callback_name, caller_path=_caller_path)
                    if lookup.found and lookup.symbol is not None:
                        callee = lookup.symbol

                if callee is None:
                    continue

                edges.append(Edge.create(
                    src=class_sym.id,
                    dst=callee.id,
                    edge_type="dispatches_to",
                    line=node.start_point[0] + 1,
                    evidence_type="ast_call_direct",
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    meta={"framework_dispatch": "rails_callback", "mechanism": "callback"},
                ))

        # Block-style callbacks: after_commit do...end or before_save { ... }
        # Extract method calls from the block body and create edges.
        block_node = None
        for child in node.children:
            if child.type in ("do_block", "block"):
                block_node = child
                break

        if block_node is not None:
            _extract_block_callback_edges(
                block_node, source, class_path,
                class_sym, file_symbols, global_symbols,
                node.start_point[0] + 1, run_id, edges,
            )

    return edges


#: Calls ``_extract_rails_routes`` records itself (with the route's method and
#: path); a registration context for them would be a second record of one call.
_ROUTE_EXTRACTOR_CALLS = HTTP_METHODS | {"resource", "resources"}


def _is_block_builder(receiver: "tree_sitter.Node", source: bytes) -> bool:
    """Whether ``receiver`` is a parameter of a block enclosing it, in the same
    class or method body: the builder a DSL yields (``register "g" do
    |prefix| prefix.register ... end``)."""
    if receiver.type != "identifier":
        return False
    name = node_text(receiver, source)
    current = receiver.parent
    while current is not None and current.type not in _RUBY_SCOPE_ROOTS:
        if current.type in ("block", "do_block"):
            params = find_child_by_type(current, "block_parameters")
            if params is not None and any(
                node_text(p, source) == name for p in params.named_children
            ):
                return True
        current = current.parent
    return False


def _registered_target(
    arg: "tree_sitter.Node",
    source: bytes,
    nesting: list[str],
    scopes: list[str],
    global_symbols: dict[str, Symbol],
    constant_ancestors: dict[str, list[str]],
) -> Optional[Symbol]:
    """The repository method or class an argument names, or ``None``.

    A symbol (``:handler``) names an instance method of the enclosing class or
    of one of its project ancestors, ``A::B#handler``. A constant names a
    class or module, looked up as Ruby looks it up (:func:`_resolve_constant`).
    Anything else (a string, a lambda, a library constant) names no local code.
    """
    if arg.type == "simple_symbol":
        method = node_text(arg, source).lstrip(":")
        for scope in scopes:
            found = global_symbols.get(f"{scope}#{method}")
            if found is not None:
                return found
        return None
    if arg.type in ("constant", "scope_resolution"):
        return _resolve_constant(
            node_text(arg, source), nesting, global_symbols, constant_ancestors,
        )
    return None


def _extract_registration_contexts(
    tree: "tree_sitter.Tree",
    source: bytes,
    rel_path: str,
    global_symbols: dict[str, Symbol],
    constant_ancestors: dict[str, list[str]],
) -> list[UsageContext]:
    """Usage contexts for calls that hand a framework LOCAL code (WI-movur).

    ``rescue_from NotFound, with: :handle_not_found``, ``field :full_name``,
    ``subcommand "remote", Remote``,
    ``register "version", Version``: a call outside every method whose
    argument names, by a symbol or a constant, a method or class of the
    repository. The framework calls that code later; nothing in the
    repository does. Each such argument gives one context: kind ``call``,
    ``context_name`` the method name, ``position`` ``args[i]`` (counting
    positional arguments) or ``kwargs[key]``, ``symbol_ref`` the local symbol.
    The frameworks' ``usage:`` rows say which calls register what.

    Recorded only when:

    - a shipped ``usage:`` row names the call AT that argument's position
      (:func:`usage_row_names_call`): the rows are the list of names and
      positions, there is none here. ``field :name, Types::UserType`` gives a
      context for ``:name`` (a row reads ``args[0]``), none for the type;
    - the call has no receiver, or ``self``, or the builder a block yields
      (``prefix.register``): one spelling, the method name, whichever it is
      (``metadata["receiver"]`` keeps the builder's name);
    - it is in no method (a class or module body, a block there, or the top
      level);
    - the class it is in, or a project ancestor, does not define the called
      name as a class method: a DSL the repository writes itself is ordinary
      code with its own call edge;
    - the name is not a route call ``_extract_rails_routes`` records.

    ``metadata`` carries what would make a row precise without a matcher
    change: the enclosing class or module (``scope``) and its written
    ``base_classes`` / ``included_modules`` (``scope_bases`` /
    ``scope_modules``).
    """
    contexts: list[UsageContext] = []
    for node in iter_tree(tree.root_node):
        if node.type != "call":
            continue
        method_node = node.child_by_field_name("method")
        args_node = node.child_by_field_name("arguments")
        if method_node is None or args_node is None or method_node.type != "identifier":
            continue
        name = node_text(method_node, source)
        if name in _ROUTE_EXTRACTOR_CALLS or not usage_row_names_call(name, "ruby"):
            continue
        receiver = node.child_by_field_name("receiver")
        if receiver is not None and not (
            receiver.type == "self" or _is_block_builder(receiver, source)
        ):
            continue
        if _is_nested_in_method(node):
            continue
        nesting = _lexical_nesting(node, source)
        scopes = [*nesting[:1], *constant_ancestors.get(nesting[0], ())] if nesting else []
        if any(f"{scope}.{name}" in global_symbols for scope in scopes):
            continue
        scope_sym = global_symbols.get(nesting[0]) if nesting else None
        scope_meta = (scope_sym.meta or {}) if scope_sym is not None else {}
        positional = 0
        for arg in args_node.named_children:
            if arg.type == "pair":
                key_node = arg.child_by_field_name("key")
                value = arg.child_by_field_name("value")
                if key_node is None or value is None:  # pragma: no cover - grammar gives both
                    continue
                position = f"kwargs[{node_text(key_node, source).strip(':')}]"
                target_node = value
            elif arg.type in ("block_argument", "splat_argument", "hash_splat_argument"):
                continue
            else:
                position = f"args[{positional}]"
                positional += 1
                target_node = arg
            if not usage_row_names_call(name, "ruby", position):
                continue
            target = _registered_target(
                target_node, source, nesting, scopes, global_symbols, constant_ancestors,
            )
            if target is None:
                continue
            contexts.append(UsageContext.create(
                kind="call",
                context_name=name,
                position=position,
                path=rel_path,
                span=Span(
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    start_col=node.start_point[1],
                    end_col=node.end_point[1],
                ),
                symbol_ref=target.id,
                metadata={
                    "receiver": node_text(receiver, source) if receiver is not None else None,
                    "argument": node_text(target_node, source),
                    "scope": nesting[0] if nesting else None,
                    "scope_bases": list(scope_meta.get("base_classes", ())),
                    "scope_modules": list(scope_meta.get("included_modules", ())),
                },
            ))
    return contexts


def _association_name_to_class(name: str) -> str:
    """Convert an ActiveRecord association name to a PascalCase class name.

    Singularizes plural association names (has_many :comments → Comment)
    and converts snake_case to PascalCase (order_item → OrderItem).

    Handles common English pluralization rules:
    - ies → y (categories → Category)
    - ses → s (addresses → Address)
    - s → '' (comments → Comment)

    Singular names (belongs_to :user) are just capitalized.
    """
    # Singularize: ies → y, ses → s, s → ''
    if name.endswith("ies"):
        name = name[:-3] + "y"
    elif name.endswith("sses"):
        name = name[:-2]
    elif name.endswith("ses"):
        name = name[:-2]
    elif name.endswith("s") and not name.endswith("ss"):
        name = name[:-1]

    # PascalCase: split on _ and capitalize each segment
    return "".join(part.capitalize() for part in name.split("_"))


def _extract_class_name_option(
    args_node: "tree_sitter.Node",
    source: bytes,
) -> str | None:
    """Extract class_name: value from association keyword arguments.

    Parses ``has_many :messages, class_name: "ChatMessage"`` to return
    ``"ChatMessage"``. Returns None if no class_name option is present.
    """
    for child in args_node.children:
        if child.type == "pair":
            key_node = child.child_by_field_name("key")
            val_node = child.child_by_field_name("value")
            if key_node is None or val_node is None:
                continue  # pragma: no cover — defensive against malformed AST
            key_text = node_text(key_node, source).lstrip(":")
            if key_text == "class_name":
                raw = node_text(val_node, source)
                # Strip surrounding quotes: "ChatMessage" or 'ChatMessage'
                return raw.strip("\"'")
    return None


def _extract_activerecord_associations(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    file_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run_id: str,
) -> list[Edge]:
    """Extract association edges from ActiveRecord model declarations.

    Rails models declare associations at class level:
        has_many :comments
        belongs_to :user
        has_one :profile
        has_many :messages, class_name: "ChatMessage"

    These create ``references`` edges (``meta["ref_construct"] =
    "association"``) from the declaring model class to the target model class. The target class is inferred from the association name
    by singularizing and converting to PascalCase, or from an explicit
    ``class_name:`` option.

    Returns ``references`` edges from class symbol to target class
    (unresolved when the target model is not in the analyzed files).
    """
    edges: list[Edge] = []

    for node in iter_tree(tree.root_node):
        if node.type != "call":
            continue

        method_node = node.child_by_field_name("method")
        if method_node is None:  # pragma: no cover
            continue

        method_name = node_text(method_node, source)
        if method_name not in _ASSOCIATION_METHODS:
            continue

        # Must be at class body level (not inside a method)
        enclosing_class_name, enclosing_type = _get_enclosing_class_or_module(
            node, source,
        )
        if enclosing_type != "class" or enclosing_class_name is None:
            continue  # pragma: no cover — defensive, associations at module level

        # Must not be inside a method
        current = node.parent
        inside_method = False
        while current is not None:
            if current.type == "method":
                inside_method = True  # pragma: no cover — defensive
                break  # pragma: no cover — defensive
            if current.type in ("class", "module"):
                break
            current = current.parent
        if inside_method:
            continue  # pragma: no cover — defensive

        # Find the class symbol, by its full lexical path (INV-dosoz)
        class_path = _lexical_nesting(node, source)[0]
        class_sym = file_symbols.get(class_path)
        if class_sym is None:  # pragma: no cover
            continue

        # Extract the association name (first symbol argument)
        args_node = _find_child_by_field(node, "arguments")
        if args_node is None:  # pragma: no cover
            continue

        assoc_name = None
        for arg in args_node.children:
            if arg.type == "simple_symbol":
                assoc_name = node_text(arg, source).lstrip(":")
                break

        if assoc_name is None:
            continue  # pragma: no cover — associations always have symbol arg

        # Determine target class name: explicit class_name option or convention
        target_class = _extract_class_name_option(args_node, source)
        if target_class is None:
            target_class = _association_name_to_class(assoc_name)

        # Look up the target class as Rails does (``compute_type``): under
        # each namespace of the declaring model, innermost first, then at the
        # top level. ``has_many :comments`` in ``Admin::Post`` is
        # ``Admin::Comment`` when that exists.
        target_sym = _resolve_constant(
            target_class, _namespaces(class_path), global_symbols,
        )

        if target_sym is not None:
            edges.append(Edge.create(
                src=class_sym.id,
                dst=target_sym.id,
                edge_type="references",
                line=node.start_point[0] + 1,
                evidence_type="ast_call_direct",
                origin=PASS_ID,
                origin_run_id=run_id,
                meta={
                    "framework_dispatch": "activerecord_association",
                    "ref_construct": "association",
                },
            ))
        else:
            # Unresolved: target model not found (may be in a gem)
            edges.append(Edge.create(
                src=class_sym.id,
                dst=f"ruby:?:0-0:{target_class}:unresolved",
                edge_type="references",
                line=node.start_point[0] + 1,
                evidence_type="ast_call_direct",
                origin=PASS_ID,
                origin_run_id=run_id,
                meta={
                    "framework_dispatch": "activerecord_association",
                    "ref_construct": "association",
                },
            ))

    return edges


# Special pseudo-associations in delegate that should be skipped.
_DELEGATE_SKIP_TARGETS = frozenset({"class"})


def _extract_to_option(
    args_node: "tree_sitter.Node",
    source: bytes,
) -> str | None:
    """Extract to: value from delegate keyword arguments.

    Parses ``delegate :foo, to: :account`` to return ``"account"``.
    Returns None if no ``to:`` option is present.
    """
    for child in args_node.children:
        if child.type == "pair":
            key_node = child.child_by_field_name("key")
            val_node = child.child_by_field_name("value")
            if key_node is None or val_node is None:
                continue  # pragma: no cover — defensive against malformed AST
            key_text = node_text(key_node, source).lstrip(":")
            if key_text == "to":
                raw = node_text(val_node, source)
                return raw.lstrip(":").strip("\"'")
    return None  # pragma: no cover — delegate without to: is invalid Ruby


def _extract_ruby_delegates(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: Path,
    file_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run_id: str,
) -> list[Edge]:
    """Extract delegate edges from Ruby class declarations.

    Rails/Ruby classes use ``delegate`` to forward method calls::

        delegate :auto_resolve_after, to: :account
        delegate :name, :email, to: :user

    This creates ``references`` edges (meta framework_dispatch
    ``ruby_delegate``) from the declaring class to the target method on the
    associated class. The target class is inferred
    by PascalCasing the ``to:`` symbol name (e.g., ``:account`` → ``Account``).

    Special targets like ``:class`` are skipped as they don't create
    meaningful cross-class edges.
    """
    edges: list[Edge] = []

    for node in iter_tree(tree.root_node):
        if node.type != "call":
            continue

        method_node = node.child_by_field_name("method")
        if method_node is None:  # pragma: no cover
            continue

        method_name = node_text(method_node, source)
        if method_name != "delegate":
            continue

        # Must be at class body level (not inside a method)
        enclosing_class_name, enclosing_type = _get_enclosing_class_or_module(
            node, source,
        )
        if enclosing_type != "class" or enclosing_class_name is None:
            continue  # pragma: no cover — delegate outside class is rare

        # Must not be inside a method
        current = node.parent
        inside_method = False
        while current is not None:
            if current.type == "method":
                inside_method = True
                break
            if current.type in ("class", "module"):
                break
            current = current.parent
        if inside_method:
            continue

        # Find the class symbol, by its full lexical path (INV-dosoz)
        class_path = _lexical_nesting(node, source)[0]
        class_sym = file_symbols.get(class_path)
        if class_sym is None:  # pragma: no cover
            continue

        # Extract arguments
        args_node = _find_child_by_field(node, "arguments")
        if args_node is None:  # pragma: no cover
            continue

        # Extract the to: target
        to_target = _extract_to_option(args_node, source)
        if to_target is None:
            continue  # pragma: no cover — delegate without to: is invalid Ruby

        # Skip special pseudo-associations
        if to_target in _DELEGATE_SKIP_TARGETS:
            continue

        # Resolve target class from to: symbol (e.g., :account → Account)
        # Looked up through the declaring class's namespaces, as the
        # association it names is.
        target_class_name = _association_name_to_class(to_target)
        target_class_sym = _resolve_constant(
            target_class_name, _namespaces(class_path), global_symbols,
        )

        # Extract all delegated method names (symbol arguments before to:)
        delegated_methods: list[str] = []
        for arg in args_node.children:
            if arg.type == "simple_symbol":
                delegated_methods.append(node_text(arg, source).lstrip(":"))

        # Create edges for each delegated method
        for method in delegated_methods:
            if target_class_sym is not None:
                # Try to find the specific method on the target class
                qualified = f"{target_class_name}#{method}"
                target_method = global_symbols.get(
                    f"{_registry_key(target_class_sym)}#{method}",
                )
                if target_method is not None:
                    edges.append(Edge.create(
                        src=class_sym.id,
                        dst=target_method.id,
                        edge_type="references",
                        line=node.start_point[0] + 1,
                        evidence_type="ast_call_direct",
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        meta={"mechanism": "delegate", "framework_dispatch": "ruby_delegate"},
                    ))
                else:
                    # Target class exists but method not found
                    edges.append(Edge.create(
                        src=class_sym.id,
                        dst=f"ruby:?:0-0:{qualified}:unresolved",
                        edge_type="references",
                        line=node.start_point[0] + 1,
                        evidence_type="ast_call_direct",
                        origin=PASS_ID,
                        origin_run_id=run_id,
                        meta={"mechanism": "delegate", "framework_dispatch": "ruby_delegate"},
                    ))
            else:
                # Target class not found at all
                qualified = f"{target_class_name}#{method}"
                edges.append(Edge.create(
                    src=class_sym.id,
                    dst=f"ruby:?:0-0:{qualified}:unresolved",
                    edge_type="references",
                    line=node.start_point[0] + 1,
                    evidence_type="ast_call_direct",
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    meta={"framework_dispatch": "ruby_delegate"},
                ))

    return edges


# NOTE: We use the base FileAnalysis from hypergumbo_core.analyze.base.
# Ruby's require_hints are stored in FileAnalysis.import_aliases.


_RUBY_ATTR_MACROS = frozenset({"attr_accessor", "attr_reader", "attr_writer"})


def _emit_ruby_data_symbol(
    analysis: FileAnalysis,
    node: "tree_sitter.Node",
    full_name: str,
    member: str,
    kind: str,
    is_exported: bool,
    source: bytes,
    file_path: str,
    file_stable_id: str,
    run_id: str,
) -> None:
    """Append one WI-jusus field/variable Symbol.

    Mirrors the class/method symbol shape (canonical ``compute_stable_id`` with a
    ``::``-qualified ``qualified_name``). The symbol is added to
    ``analysis.symbols`` only — NOT ``symbol_by_name`` — so a data anchor never
    shadows a same-named method during per-file resolution; the
    ``register_symbol`` chokepoint likewise keeps it out of the global registry.
    """
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    ruby_chain = _get_ruby_class_or_module_chain(node, source)
    qualified = _make_ruby_qualified_name(ruby_chain, member)
    symbol = Symbol(
        id=make_symbol_id(
            "ruby", str(file_path), start_line, end_line, full_name, kind,
        ),
        name=full_name,
        kind=kind,
        language="ruby",
        path=str(file_path),
        span=Span(
            start_line=start_line,
            end_line=end_line,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
        ),
        origin=PASS_ID,
        origin_run_id=run_id,
        is_exported=is_exported,
        line_span=end_line - start_line + 1,
        qualified_name=qualified,
        stable_id=_analyzer.compute_stable_id(
            node, kind=kind, name=full_name,
            qualified_name=qualified, file_stable_id=file_stable_id,
        ),
        shape_id=_analyzer.compute_shape_id(node),
    )
    analysis.symbols.append(symbol)
    analysis.node_for_symbol[symbol.id] = node


def _emit_ruby_data_symbols(
    analysis: FileAnalysis,
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    file_stable_id: str,
    run_id: str,
) -> None:
    """Emit kind="field" / kind="variable" data anchors for Ruby (WI-jusus).

    Ruby has no lexical field declaration, so the statically-recoverable declared
    data members are: class/module-body CONSTANTS and ``@@`` class variables
    (kind=field, owner = the enclosing class/module), top-level CONSTANTS
    (kind=variable), and ``attr_accessor`` / ``attr_reader`` / ``attr_writer``
    attributes (kind=field). Members assigned inside a method body — instance
    variables (``@x``) and method-local constants — are deferred: attributing
    them to the enclosing class needs a distinct mechanism, so they are skipped
    via ``_is_nested_in_method``.
    """
    for node in iter_tree(tree.root_node):
        if node.type == "assignment":
            left = _find_child_by_field(node, "left")
            if left is None or left.type not in ("constant", "class_variable"):
                continue
            if _is_nested_in_method(node):
                continue
            member = node_text(left, source)
            enclosing_name, _enclosing_type = _get_enclosing_class_or_module(
                node, source,
            )
            if enclosing_name is not None:
                # A class/module-body constant or ``@@`` class variable is a
                # field of that type. Constants are public by convention; ``@@``
                # class variables are class-internal state.
                _emit_ruby_data_symbol(
                    analysis, left, f"{enclosing_name}::{member}", member,
                    "field", left.type == "constant",
                    source, file_path, file_stable_id, run_id,
                )
            elif left.type == "constant":
                # Top-level constant -> module-level variable. (A top-level
                # ``@@`` class variable is not valid Ruby, so only constants
                # reach this branch.)
                _emit_ruby_data_symbol(
                    analysis, left, member, member, "variable", True,
                    source, file_path, file_stable_id, run_id,
                )
        elif node.type == "call":
            method_node = _find_child_by_field(node, "method")
            if (
                method_node is None
                or node_text(method_node, source) not in _RUBY_ATTR_MACROS
            ):
                continue
            if _is_nested_in_method(node):
                continue
            enclosing_name, _enclosing_type = _get_enclosing_class_or_module(
                node, source,
            )
            if enclosing_name is None:
                continue
            args_node = _find_child_by_field(node, "arguments")
            if args_node is None:  # pragma: no cover - a call always has an argument_list field
                continue
            for arg in args_node.children:
                if arg.type != "simple_symbol":
                    continue
                attr = node_text(arg, source).lstrip(":")
                if not attr:  # pragma: no cover - a simple_symbol always names an attribute
                    continue
                _emit_ruby_data_symbol(
                    analysis, arg, f"{enclosing_name}#{attr}", attr,
                    "field", True,
                    source, file_path, file_stable_id, run_id,
                )


def _extract_symbols_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    run_id: str,
) -> FileAnalysis:
    """Extract symbols from a single Ruby file.

    Args:
        tree: Parsed tree-sitter tree
        source: Raw source bytes
        file_path: Relative path to the file
        run_id: Execution ID for provenance
    """
    analysis = FileAnalysis()
    # File-identity anchor for this file's symbols (WI-bokab). ``file_path`` is
    # the repo-relative path (the analyze() override passes ``rel_path`` =
    # ``rb_file.relative_to(repo_root)``). Folded into compute_stable_id's
    # containing slot so same-name methods/classes/modules in different files hash
    # distinctly. Reuses the base helper (= make_file_stable_id("ruby",
    # normalize_path(file_path))) so it byte-matches any sibling producer's anchor.
    file_stable_id = _analyzer._file_anchor(file_path)

    for node in iter_tree(tree.root_node):
        # Method definition (instance methods)
        if node.type == "method":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                method_name = node_text(name_node, source)
                # Qualify with class/module name if inside one
                enclosing_name, enclosing_type = _get_enclosing_class_or_module(node, source)
                if enclosing_type == "class" and enclosing_name:
                    full_name = f"{enclosing_name}#{method_name}"
                elif enclosing_type == "module" and enclosing_name:
                    full_name = f"{enclosing_name}.{method_name}"
                else:
                    full_name = method_name

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                ruby_chain = _get_ruby_class_or_module_chain(node, source)
                member_bases = _public_member_bases(node, source)
                symbol = Symbol(
                    id=make_symbol_id("ruby", str(file_path), start_line, end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="ruby",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    meta={"parent_base_classes": member_bases} if member_bases else None,
                    signature=_extract_ruby_signature(node, source),
                    docstring=extract_preceding_doc_comment(node, source, "ruby"),
                    stable_id=_analyzer.compute_stable_id(
                        node, kind="method", name=full_name,
                        qualified_name=_make_ruby_qualified_name(ruby_chain, method_name),
                        file_stable_id=file_stable_id,
                    ),
                    shape_id=_analyzer.compute_shape_id(node),
                    line_span=end_line - start_line + 1,
                    is_exported=not _is_nested_in_method(node),
                    qualified_name=_make_ruby_qualified_name(ruby_chain, method_name),
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "ruby"),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[method_name] = symbol
                analysis.symbol_by_name[_registry_key(symbol)] = symbol

        # Singleton method definition (class methods: def self.method_name)
        elif node.type == "singleton_method":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                method_name = node_text(name_node, source)
                # Class methods use dot separator: ClassName.method_name
                enclosing_name, _ = _get_enclosing_class_or_module(node, source)
                if enclosing_name:
                    full_name = f"{enclosing_name}.{method_name}"
                else:
                    full_name = method_name

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                ruby_chain = _get_ruby_class_or_module_chain(node, source)
                symbol = Symbol(
                    id=make_symbol_id("ruby", str(file_path), start_line, end_line, full_name, "method"),
                    name=full_name,
                    kind="method",
                    language="ruby",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    signature=_extract_ruby_signature(node, source),
                    docstring=extract_preceding_doc_comment(node, source, "ruby"),
                    stable_id=_analyzer.compute_stable_id(
                        node, kind="method", name=full_name,
                        qualified_name=_make_ruby_qualified_name(ruby_chain, method_name),
                        file_stable_id=file_stable_id,
                    ),
                    shape_id=_analyzer.compute_shape_id(node),
                    line_span=end_line - start_line + 1,
                    is_exported=not _is_nested_in_method(node),
                    qualified_name=_make_ruby_qualified_name(ruby_chain, method_name),
                    cyclomatic_complexity=compute_cyclomatic_complexity(node, "ruby"),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[method_name] = symbol
                analysis.symbol_by_name[_registry_key(symbol)] = symbol

        # Class definition
        elif node.type == "class":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                class_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Extract superclass if present (META-001)
                meta: dict[str, object] | None = None
                superclass_node = _find_child_by_field(node, "superclass")
                if superclass_node:
                    # The superclass node contains a child with the base class name
                    # For "class User < BaseModel": superclass has constant "BaseModel"
                    # For "class User < ActiveRecord::Base": superclass has scope_resolution
                    # We take the first named child which is the actual type reference
                    for child in superclass_node.children:
                        if child.is_named:
                            superclass_name = node_text(child, source)
                            meta = {"base_classes": [superclass_name]}
                            break

                # Extract `include` / `extend` mixin declarations as
                # `included_modules` so the linker stack can walk through
                # them for inherited-call resolution (WI-hatip).
                body_node = _find_child_by_field(node, "body")
                if body_node is not None:
                    included = _extract_included_modules(body_node, source)
                    if included:
                        if meta is None:
                            meta = {}
                        meta["included_modules"] = included

                ruby_chain = _get_ruby_class_or_module_chain(node, source)
                symbol = Symbol(
                    id=make_symbol_id("ruby", str(file_path), start_line, end_line, class_name, "class"),
                    name=class_name,
                    kind="class",
                    language="ruby",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    meta=meta,
                    stable_id=_analyzer.compute_stable_id(
                        node, kind="class", name=class_name,
                        qualified_name=_make_ruby_qualified_name(ruby_chain, class_name),
                        file_stable_id=file_stable_id,
                    ),
                    shape_id=_analyzer.compute_shape_id(node),
                    line_span=end_line - start_line + 1,
                    is_exported=not _is_nested_in_method(node),
                    qualified_name=_make_ruby_qualified_name(ruby_chain, class_name),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[_registry_key(symbol)] = symbol

        # Module definition
        elif node.type == "module":
            name_node = _find_child_by_field(node, "name")
            if name_node:
                module_name = node_text(name_node, source)
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                # Modules can include other modules — same MRO contribution
                # as on classes (WI-hatip).
                module_meta: dict[str, object] | None = None
                body_node = _find_child_by_field(node, "body")
                if body_node is not None:
                    included = _extract_included_modules(body_node, source)
                    if included:
                        module_meta = {"included_modules": included}

                ruby_chain = _get_ruby_class_or_module_chain(node, source)
                symbol = Symbol(
                    id=make_symbol_id("ruby", str(file_path), start_line, end_line, module_name, "module"),
                    name=module_name,
                    kind="module",
                    language="ruby",
                    path=str(file_path),
                    span=Span(
                        start_line=start_line,
                        end_line=end_line,
                        start_col=node.start_point[1],
                        end_col=node.end_point[1],
                    ),
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    meta=module_meta,
                    stable_id=_analyzer.compute_stable_id(
                        node, kind="module", name=module_name,
                        qualified_name=_make_ruby_qualified_name(ruby_chain, module_name),
                        file_stable_id=file_stable_id,
                    ),
                    shape_id=_analyzer.compute_shape_id(node),
                    line_span=end_line - start_line + 1,
                    is_exported=not _is_nested_in_method(node),
                    qualified_name=_make_ruby_qualified_name(ruby_chain, module_name),
                )
                analysis.symbols.append(symbol)
                analysis.node_for_symbol[symbol.id] = node
                analysis.symbol_by_name[_registry_key(symbol)] = symbol

    # Extract require hints for disambiguation (stored in import_aliases)
    analysis.import_aliases = _extract_require_hints(tree, source)

    # Emit kind=field/variable data anchors (constants, attr_* attributes,
    # class variables; WI-jusus). Kept out of symbol_by_name / the call
    # registry (see _emit_ruby_data_symbols and register_symbol).
    _emit_ruby_data_symbols(
        analysis, tree, source, file_path, file_stable_id, run_id,
    )

    return analysis


# ActiveJob/Sidekiq class-method names that enqueue a job.
# When we see SomeJob.perform_later(args), we redirect to SomeJob#perform.
_JOB_ENQUEUE_METHODS = frozenset({"perform_later", "perform_async", "perform_in",
                                   "perform_at"})


def _extract_included_modules(
    body_node: "tree_sitter.Node",
    source: bytes,
) -> list[str]:
    """Scan top-level body statements for ``include`` / ``extend`` declarations.

    Ruby's mixin idioms (`include Mod`, `extend Mod`) contribute to the
    method-resolution order at runtime. They are extracted as
    ``meta["included_modules"]`` on the enclosing class/module Symbol so the
    centralized ``inheritance.py`` linker can emit ``includes`` edges and the
    ``inherited_calls.py`` linker can walk through them as part of the Ruby
    insertion-order MRO (WI-hatip).

    Only **literal constant** arguments are captured (``constant`` and
    ``scope_resolution`` AST shapes). Dynamic ``include some_method_call``
    forms are silently dropped because they cannot be statically resolved
    to a project Symbol without a name resolver.

    Args:
        body_node: The ``body_statement`` child of a ``class`` or ``module``
            node.
        source: Raw source bytes (for `node_text` extraction).

    Returns:
        Declaration-ordered list of module name strings (qualified form
        preserved, e.g. ``"Sidekiq::Worker"``). Empty list if no matching
        declarations are present.
    """
    modules: list[str] = []
    for child in body_node.children:
        if child.type != "call":
            continue
        method_node = _find_child_by_field(child, "method")
        if method_node is None or method_node.type != "identifier":  # pragma: no cover - defensive: tree-sitter Ruby grammar always emits a "method" field on `call` nodes; non-identifier method (e.g. operator) skipped
            continue
        method_name = node_text(method_node, source)
        if method_name not in ("include", "extend"):
            continue
        args_node = _find_child_by_field(child, "arguments")
        if args_node is None:  # pragma: no cover - defensive: tree-sitter `call` with bare `include` would parse as identifier, not call, so this branch is unreachable in practice
            continue
        for arg in args_node.children:
            if arg.type in ("constant", "scope_resolution"):
                modules.append(node_text(arg, source))
    return modules


def _lexical_nesting(node: "tree_sitter.Node", source: bytes) -> list[str]:
    """``Module.nesting`` at ``node``: the enclosing modules / classes as full
    lexical paths, innermost first (WI-gomup).

    ``module A; module B`` gives ``["A::B", "A"]``. A compact ``class A::C``
    is ONE level whose name is ``A::C`` -- Ruby does not add ``A`` to the
    nesting -- so inside it the nesting is ``["A::C"]``. A level written with
    a root ``::`` restarts the path (:func:`_lexical_path`). Positional: read
    off the call site's ancestors, never looked up by name. Each level is the
    registry key of the class or module declared there
    (:func:`_registry_key`).
    """
    names = _get_ruby_class_or_module_chain(node, source)  # outermost first
    return [
        _lexical_path("::".join(names[: i + 1]))
        for i in range(len(names) - 1, -1, -1)
    ]


def _declared_once(
    key: str, global_symbols: dict[str, Symbol], shared_keys: frozenset[str],
) -> Optional[Symbol]:
    """The method registered under the full lexical ``key``, unless two FILES
    declare it (a class reopened in two files that each define the method):
    Ruby keeps whichever file was loaded last, which is the build's order and
    not in the source, so neither is the callee by the source's evidence. In
    one file the later definition replaces the earlier one, Ruby's own rule,
    and the registry holds the later."""
    return None if key in shared_keys else global_symbols.get(key)


def _try_receiver_call(
    receiver_node: "tree_sitter.Node",
    method_name: str,
    source: bytes,
    current_method: Symbol,
    global_symbols: dict[str, Symbol],
    line: int,
    edges: list[Edge],
    run_id: str,
    constant_ancestors: Optional[dict[str, list[str]]] = None,
    shared_keys: frozenset[str] = frozenset(),
) -> bool:
    """Try to resolve a receiver-qualified method call.

    Handles constant receivers (User.find), scope_resolution receivers
    (ActiveRecord::Base.connection) and a construction chained on either
    (``Voice::Builder.new(...).perform``, a call on the instance). The
    receiver constant is resolved FIRST, to the class or module Ruby's lookup
    gives it (:func:`_resolve_constant`: the call site's lexical nesting
    innermost out, the ancestors of the innermost scope, then the top level,
    by full path), and the method is then looked up on that class alone, in
    the declarations of ONE file (:func:`_declared_once`). A class that does
    not declare it leaves an unresolved edge naming the class by its full
    path (ADR-0064): ``qualifier`` for ``Const.m`` (and ``Const.new``, whose
    callee is ``#initialize``), ``receiver_type_hint`` for a call on
    ``Const.new(...)``, and the inherited-calls linker walks its ancestors.
    Never a bind to a same-named class elsewhere.

    A call chained on any other method of a constant (``Model.where(..).first``)
    is a call on whatever that method returns, which the source does not say:
    it is not this function's (False).

    Special case: ActiveJob/Sidekiq enqueue methods (perform_later, perform_async,
    perform_in, perform_at) are redirected to the job class's ``perform`` instance
    method and emit ``event_publishes`` edges instead of ``calls``.

    Returns True if an edge was created, False to fall through.
    """
    # The receiver constant as written: a constant, a scope_resolution
    # (``Voice::InboundCallBuilder``, ``::Redis::Alfred``), or the receiver of
    # a chained call -- ``ClassName.new(args).method()``,
    # ``ClassName.set(wait: 1.hour).perform_later(args)``.
    receiver_class: str | None = None
    constructed = False
    if receiver_node.type in ("constant", "scope_resolution"):
        receiver_class = node_text(receiver_node, source)
    elif receiver_node.type == "call":
        inner_receiver = receiver_node.child_by_field_name("receiver")
        inner_method = receiver_node.child_by_field_name("method")
        if inner_receiver is not None and inner_receiver.type in (
            "constant", "scope_resolution",
        ):
            receiver_class = node_text(inner_receiver, source)
            constructed = (
                inner_method is not None and node_text(inner_method, source) == "new"
            )

    if receiver_class is None:
        return False

    owner = _resolve_constant(
        receiver_class, _lexical_nesting(receiver_node, source), global_symbols,
        constant_ancestors,
    )

    # Detect ActiveJob/Sidekiq enqueue patterns:
    # SomeJob.perform_later(args) -> enqueues SomeJob#perform
    # SomeJob.set(wait: 1.hour).perform_later(args) -> enqueues SomeJob#perform
    if method_name in _JOB_ENQUEUE_METHODS:
        return _try_job_enqueue(
            receiver_class, owner, current_method,
            global_symbols, line, edges, run_id,
        )

    if receiver_node.type == "call" and not constructed:
        return False

    if owner is not None:
        owner_key = _registry_key(owner)
        if constructed:
            # ``Const.new(...).m``: an instance of the class, whose instance
            # method is the callee.
            callee = _declared_once(f"{owner_key}#{method_name}", global_symbols, shared_keys)
            if callee is not None and callee.id != current_method.id:
                edges.append(Edge.create(
                    src=current_method.id,
                    dst=callee.id,
                    edge_type="calls",
                    line=line,
                    evidence_type="ast_call",
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    meta={"call_construct": "method", "receiver": "generic"},
                    resolved_by=resolved_by.RECEIVER_TYPE,
                ))
                return True
            edges.append(_ruby_stub(
                current_method, method_name, line, run_id,
                module_hint=owner.name, receiver_type_hint=owner_key,
                call_construct="method",
            ))
            return True
        # Ruby constructor: SomeClass.new → SomeClass#initialize
        # In Ruby, .new is the class-level allocator that delegates to #initialize.
        # A Rails controller ``def new`` is an instance method (action), not the
        # constructor.  Matching ``SomeClass.new`` to ``SomeClass#new`` would bind
        # object construction to controller ``new`` actions, a large source of
        # false-positive edges.  Redirect to #initialize instead.
        if method_name == "new":
            callee = _declared_once(f"{owner_key}#initialize", global_symbols, shared_keys)
            if callee is not None and callee.id != current_method.id:
                edges.append(Edge.create(
                    src=current_method.id,
                    dst=callee.id,
                    # Object creation is ``instantiates``, as in every other
                    # analyzer that emits it (dart/csharp/java/py/js_ts/php/
                    # cpp/verilog), so a consumer asking for object creation
                    # by edge_type sees ruby too. The DST is the initializer
                    # symbol (``Klass.new`` -> ``Klass#initialize``), as in
                    # csharp: anchoring on the class would conflate
                    # "referenced as a type" with "instantiated" and break
                    # reverse-slice (INV-kahig, WI-fagit).
                    edge_type="instantiates",
                    line=line,
                    evidence_type="ast_call",
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    meta={"call_construct": "constructor"},
                    resolved_by=resolved_by.QUALIFIED,
                ))
                return True
            # ``Klass#initialize`` is absent on the named class (or two files
            # declare it): an unresolved call of ``initialize`` qualified by
            # the class's full path. The ``inherited_calls`` linker
            # (priority=18) finds the class through the file's table and
            # walks its ancestor chain (extends + includes, the Ruby
            # insertion-order MRO), so mixin contributions (Sidekiq::Worker,
            # ActiveModel::Validations, ...) are reached through
            # ``included_modules`` metadata + ``includes`` edges (WI-hatip;
            # see INV-nilud).
            edges.append(_ruby_stub(
                current_method, "initialize", line, run_id,
                module_hint=owner.name, qualifier=owner_key,
            ))
            return True

        # Class#method (instance method convention), then Class.method
        # (module method), on the resolved class.
        for sep in ("#", "."):
            callee = _declared_once(f"{owner_key}{sep}{method_name}", global_symbols, shared_keys)
            if callee is not None and callee.id != current_method.id:
                edges.append(Edge.create(
                    src=current_method.id,
                    dst=callee.id,
                    edge_type="calls",
                    line=line,
                    evidence_type="ast_call",
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    meta={"call_construct": "method", "receiver": "generic"},
                    resolved_by=resolved_by.QUALIFIED,
                ))
                return True

        # The receiver NAMES its owner, so the method is that owner's or an
        # ancestor's, never whichever class happens to declare a method of the
        # same short name. A receiver-blind lookup would bind ``Rails.root`` to
        # ``HealthServer#root`` and ``File.read(p)`` to a project's own ``#read``
        # (defeating a user's overlay row for it) (WI-johib). A project constant
        # that does not declare the method keeps an unresolved edge qualified
        # by the class's full path, and the inherited-calls linker walks its
        # ancestors (``Kid.make`` -> ``Base.make``).
        edges.append(_ruby_stub(
            current_method, method_name, line, run_id,
            module_hint=owner.name, qualifier=owner_key,
            call_construct="method",
        ))
        return True

    if constructed:
        # ``Set.new.add(x)``: an instance of a class the project does not
        # declare. Its type, as written, is all the linkers need to leave it
        # alone: no declaration of the repository is that type.
        edges.append(_ruby_stub(
            current_method, method_name, line, run_id,
            receiver_type_hint=receiver_class.lstrip(":"), call_construct="method",
        ))
        return True

    # Constant-receiver call (``JSON.parse``, ``Set.new``) on a constant the
    # project declares no class or module for at any path Ruby would try. It
    # gets an unresolved edge whose module slot names the receiver constant,
    # so io-boundary catalogues and user overlays can match the call against
    # ruby stdlib / gem primitives (WI-rijij, WI-mafik).
    #
    # The slot is the constant AS WRITTEN, less a root ``::`` --
    # ``Net::HTTP``, ``File``, ``JSON``. ADR-0051 says the slot names the
    # OWNER PATH of the called symbol, and in ruby that is the constant the
    # call is made on. It is not cut to its last segment or lowercased: an
    # overlay row spelled as ruby writes it (``module: Net::HTTP``) must
    # match, a component-suffix match must agree in case (INV-dijor), and
    # the namespace is the only thing telling ``Net::HTTP`` from
    # ``Faraday::HTTP`` (WI-surar).
    #
    # THE REQUIRE HINT IS DELIBERATELY NOT CONSULTED HERE. It maps a require
    # path's PascalCased basename to that path (``require 'set'`` gives
    # ``Set -> set``): evidence of which file was loaded, keyed on one
    # segment, so it names a file rather than the owner and would re-drop the
    # namespace (``Foo::Client`` would take ``require 'bar/client'``). It
    # still serves bare-call disambiguation and base-class resolution. The
    # matcher folds ``::`` and ``/`` alike and compares case-insensitively on
    # equal paths, so ``Net::HTTP`` still matches a ``net/http`` row, and
    # ``Set``/``JSON`` still match ``set``/``json``.
    module_hint = receiver_class.lstrip(":")
    edges.append(Edge.create(
        src=current_method.id,
        dst=f"ruby:{module_hint}:0-0:{method_name}:unresolved",
        edge_type="calls",
        line=line,
        evidence_type="ast_call_direct",
        is_resolved=False,
        origin=PASS_ID,
        origin_run_id=run_id,
        meta={"call_construct": "method", "receiver": "constant_external"},
        dst_ref=ExternalRef(
            lang="ruby", module_path=module_hint, name=method_name,
        ),
    ))
    return True


def _typed_send(
    written: str,
    method_name: str,
    receiver_node: "tree_sitter.Node",
    source: bytes,
    current_method: Symbol,
    global_symbols: dict[str, Symbol],
    constant_ancestors: Optional[dict[str, list[str]]],
    shared_keys: frozenset[str],
    line: int,
    edges: list[Edge],
    run_id: str,
) -> None:
    """``v.m`` where ``v`` was assigned ``Const.new(...)`` in the same method
    (:func:`_extract_ruby_var_types`). ``Const`` is resolved as a constant
    written at the call is (:func:`_resolve_constant`), and the call bound to
    that class's own instance method ``#m`` declared in one file
    (``receiver_type``). Otherwise reported with the type (ADR-0064): the
    class's full path when the project declares it (the inherited-calls
    linker walks its ancestors: ``Child.new.ping`` reaches ``Base#ping``),
    else the constant as written (a library's class, which no declaration of
    the repository is). Before, both cases emitted no edge at all."""
    typed = _resolve_constant(
        written, _lexical_nesting(receiver_node, source),
        global_symbols, constant_ancestors,
    )
    if typed is not None:
        callee = _declared_once(
            f"{_registry_key(typed)}#{method_name}", global_symbols, shared_keys,
        )
        if callee is not None and callee.id != current_method.id:
            edges.append(Edge.create(
                src=current_method.id,
                dst=callee.id,
                edge_type="calls",
                line=line,
                evidence_type="ast_call",
                origin=PASS_ID,
                origin_run_id=run_id,
                meta={"call_construct": "method", "resolution_quality": "typed_receiver"},
                resolved_by=resolved_by.RECEIVER_TYPE,
            ))
            return
    type_name = _registry_key(typed) if typed is not None else written.lstrip(":")
    edges.append(_ruby_stub(
        current_method, method_name, line, run_id,
        module_hint=typed.name if typed is not None else type_name,
        receiver_type_hint=type_name, call_construct="method",
    ))


def _try_job_enqueue(
    receiver_class: str,
    job_class: Optional[Symbol],
    current_method: Symbol,
    global_symbols: dict[str, Symbol],
    line: int,
    edges: list[Edge],
    run_id: str,
) -> bool:
    """Create an event_publishes edge from current method to a job's perform method.

    When SomeJob.perform_later(args) is called, the actual work is done by
    SomeJob#perform at runtime. ``job_class`` is the class the receiver
    constant resolved to (:func:`_resolve_constant`), or ``None``; the edge
    reaches its ``perform`` method.

    Falls back to an edge targeting the job class symbol if it declares no
    ``perform``, and to an unresolved edge naming the receiver as written if
    the project declares no such class (e.g., job defined in a gem).
    """
    if job_class is not None:
        # JobClass#perform, then JobClass.perform, then the class itself.
        targets = [
            global_symbols.get(f"{_registry_key(job_class)}{sep}perform")
            for sep in ("#", ".")
        ]
        for callee in (*targets, job_class):
            if callee is not None and callee.id != current_method.id:
                # ActiveJob perform_later enqueues a job (queue
                # is the channel kind). Canonical 'event_publishes'
                # + meta['channel_kind']='queue' (ADR-0023 §6 Phase 3;
                # audit-findings 0001).
                edges.append(Edge.create(
                    src=current_method.id,
                    dst=callee.id,
                    edge_type="event_publishes",
                    line=line,
                    evidence_type="ast_call_direct",
                    origin=PASS_ID,
                    origin_run_id=run_id,
                    meta={"channel_kind": "queue", "framework_dispatch": "job_enqueue"},
                ))
                return True

    # Last resort: unresolved edge to the job class name
    edges.append(Edge.create(
        src=current_method.id,
        dst=f"ruby:?:0-0:{receiver_class}:unresolved",
        edge_type="event_publishes",
        line=line,
        evidence_type="ast_call_direct",
        origin=PASS_ID,
        origin_run_id=run_id,
        meta={"channel_kind": "queue", "framework_dispatch": "job_enqueue"},
    ))
    return True


def _extract_ruby_var_types(
    tree: "tree_sitter.Tree",
    source: bytes,
) -> ScopedVarTypes:
    """Extract variable-to-class-name mappings from constructions.

    ``var = ClassName.new(...)`` and ``var = A::B.new(...)`` give *var* the
    class as written. Only a construction: what any other method of a class
    returns (``File.read(p)`` a String, ``Model.where(..)`` a relation,
    ``Model.find(id)`` a record only by ActiveRecord's convention) the source
    does not say, and a type read off the receiver of such a call bound
    ``x.split`` to ``File#split`` (ADR-0064: unresolved beats a guess).

    SCOPE (INV-zabud). A binding belongs to the method (``def``, ``def
    self.``) that makes it; the first assignment to a name IN THAT METHOD
    wins. An assignment outside every method is seen by all of them. Ask the
    result for the bindings a call sees: ``var_types.at(call_node)``.
    """
    var_types = ScopedVarTypes(remember=True)
    for node in iter_tree(tree.root_node):
        var_types.close_scopes_before(node)
        if node.type in ("method", "singleton_method"):
            var_types.open_scope(node)
            continue
        if node.type != "assignment":
            continue
        # LHS must be a simple identifier
        lhs = node.child_by_field_name("left")
        if lhs is None or lhs.type != "identifier":
            continue
        var_name = node_text(lhs, source)
        if var_types.bound_here(var_name):
            continue  # first assignment in this method wins
        # RHS must be a call node with a constant receiver
        rhs = node.child_by_field_name("right")
        if rhs is None or rhs.type != "call":
            continue
        receiver = rhs.child_by_field_name("receiver")
        method = rhs.child_by_field_name("method")
        if (
            receiver is not None and receiver.type in ("constant", "scope_resolution")
            and method is not None and node_text(method, source) == "new"
        ):
            var_types[var_name] = node_text(receiver, source)
    return var_types


def _require_relative_target(abs_path: Path, rel_path: str, written: str) -> Optional[str]:
    """The file node a ``require_relative`` names, when the tree holds it.

    Ruby reads the name next to the requiring file and adds ``.rb`` when it
    has no extension (``edge_target.in_tree_file_id``, which also skips a
    file outside the repository or one discovery leaves out). ``None``: the
    caller keeps the stand-in for the name as written. ``require`` is read
    along ``$LOAD_PATH``, the build's configuration, and is not looked up.
    """
    joined = posixpath.join(posixpath.dirname(rel_path), written)
    candidates = [joined] if joined.endswith(".rb") else [joined + ".rb", joined]
    return in_tree_file_id(abs_path, rel_path, candidates)


def _extract_edges_from_file(
    tree: "tree_sitter.Tree",
    source: bytes,
    file_path: str,
    local_symbols: dict[str, Symbol],
    global_symbols: dict[str, Symbol],
    run_id: str,
    resolver: NameResolver,
    superclass_by_qualified: dict[str, str],
    file_symbols: list[Symbol] | None = None,
    constant_ancestors: Optional[dict[str, list[str]]] = None,
    abs_path: Optional[Path] = None,
    shared_keys: frozenset[str] = frozenset(),
) -> list[Edge]:
    """Extract call and import edges from a file.

    ``abs_path`` is the file on disk; with ``file_path`` it gives the
    repository root a ``require_relative`` lookup needs
    (:func:`_require_relative_target`). Without it the lookup is skipped.

    Args:
        tree: Parsed tree-sitter tree
        source: Raw source bytes
        file_path: Relative path to the file
        local_symbols: File-local symbol lookup dict
        global_symbols: Global symbol lookup dict
        run_id: Execution ID for provenance
        resolver: Name resolver for a first-class method reference
            (``method(:name)``) and a hash value naming a method; no call
            route reads it (ADR-0064).
        superclass_by_qualified: Run-wide ``{qualified class name:
            superclass}`` (:func:`superclasses_by_qualified_name`), for a
            ``super`` in a reopened class that names no superclass itself.
        constant_ancestors: Run-wide ``{class path: ancestor paths}``
            (:func:`_constant_ancestors`), for a constant receiver a class
            reaches through its superclass or an included module.
        shared_keys: The method keys two files declare (:func:`_declared_once`).
    """
    # Every declaration of this file, by position (INV-midag): ``local_symbols``
    # keeps ONE symbol per name.
    decl_symbols = (
        file_symbols if file_symbols is not None
        else list({s.id: s for s in local_symbols.values()}.values()))
    decl_index = symbols_at(decl_symbols)
    _caller_path = str(file_path)
    edges: list[Edge] = []
    # WI-misir: per-scope local bindings, computed once per scope.
    local_scope_cache: dict[tuple[int, int], list[tuple[str, int, int, int]]] = {}
    file_id = make_file_id("ruby", str(file_path))
    # The anchor of last resort for a call in no method, class or module.
    file_anchor = file_anchor_symbol("ruby", str(file_path), PASS_ID, run_id)
    var_types = _extract_ruby_var_types(tree, source)

    for node in iter_tree(tree.root_node):
        # Detect call nodes (require statements and method calls)
        if node.type == "call":
            # Get method name from the 'method' field (not the first identifier!)
            # For `data.chop`, the tree has:
            #   call: receiver=identifier("data"), method=identifier("chop")
            # We want "chop", not "data"
            method_node = node.child_by_field_name("method")
            # Fallback for simple calls without receiver (e.g., `require "foo"`)
            # All tested call forms have method field, but this handles potential
            # edge cases in tree-sitter-ruby grammar versions.
            if method_node is None:  # pragma: no cover
                for child in node.children:
                    if child.type == "identifier":
                        method_node = child
                        break

            if method_node:
                callee_name = node_text(method_node, source)
                # Error recovery's zero-width MISSING method (`foo.`) names
                # nothing; an edge to it would be `ruby:external:0-0::`
                # (WI-pusan).
                if not callee_name:
                    continue

                # Handle require/require_relative as imports
                if callee_name in ("require", "require_relative"):
                    args_node = _find_child_by_field(node, "arguments")
                    if args_node:
                        for arg in args_node.children:
                            if arg.type == "string":
                                content_node = find_child_by_type(arg, "string_content")
                                if content_node:
                                    import_path = node_text(content_node, source)
                                    found = (
                                        _require_relative_target(abs_path, file_path, import_path)
                                        if callee_name == "require_relative" and abs_path is not None
                                        else None
                                    )
                                    edges.append(Edge.create(
                                        src=file_id,
                                        dst=found or f"ruby:{import_path}:0-0:file:file",
                                        edge_type="imports",
                                        line=node.start_point[0] + 1,
                                        evidence_type="require_statement",
                                        is_resolved=found is not None,
                                        origin=PASS_ID,
                                        origin_run_id=run_id,
                                    ))

                # method(:name) — first-class method reference. Only self's:
                # ``obj.method(:x)`` / ``1.method(:+)`` name a method of the
                # receiver's class, which this pass does not know, and binding
                # the bare name there reached an unrelated ``Enumerator#+``.
                elif callee_name == "method" and _receiver_is_self(node, source):
                    args_node = _find_child_by_field(node, "arguments")
                    if args_node is not None:
                        sym_node = find_child_by_type(args_node, "simple_symbol")
                        if sym_node is not None:
                            ref_name = node_text(sym_node, source).lstrip(":")
                            # A reference in no method (a constant at top
                            # level or in a class body) is anchored as a call
                            # there is: the class or module whose body holds
                            # it, else the file (META-mino, INV-bamij).
                            current_method: Optional[Symbol] = (
                                _get_enclosing_method(node, source, decl_index)
                                or enclosing_declared_symbol(
                                    node, decl_index, _SCOPE_BODY_NODES,
                                )
                                or file_anchor
                            )
                            if current_method is not None:
                                # Class-qualified lookup: App#transform, by
                                # the class's lexical path. A class-body
                                # anchor IS the class.
                                enc_class = (
                                    _registry_key(current_method)
                                    if current_method.kind in ("class", "module")
                                    else _owner_path(current_method)
                                )
                                target = None
                                if enc_class:
                                    target = local_symbols.get(
                                        f"{enc_class}#{ref_name}",
                                    )
                                    if target is None:
                                        target = global_symbols.get(
                                            f"{enc_class}#{ref_name}",
                                        )
                                if target is None:
                                    target = local_symbols.get(ref_name)
                                if target is None:
                                    lookup = resolver.lookup(ref_name, caller_path=_caller_path)
                                    if lookup.found and lookup.symbol is not None:
                                        target = lookup.symbol
                                if (
                                    target is not None
                                    and target.kind in ("function", "method")
                                    and target.id != current_method.id
                                ):
                                    edges.append(Edge.create(
                                        src=current_method.id,
                                        dst=target.id,
                                        edge_type="references",
                                        line=node.start_point[0] + 1,
                                        evidence_type="method_reference",
                                        origin=PASS_ID,
                                        origin_run_id=run_id,
                                    ))

                # Handle regular method calls
                else:
                    # A call in no method is anchored on the class or module
                    # whose body holds it, else on the FILE, as python and php
                    # anchor module-level code. Top-level code is the norm in
                    # Ruby scripts, Rakefiles and config/*.rb (INV-bamij).
                    current_method = (
                        _get_enclosing_method(node, source, decl_index)
                        or enclosing_declared_symbol(node, decl_index, _SCOPE_BODY_NODES)
                        or file_anchor
                    )
                    if current_method is not None:
                        receiver_node = node.child_by_field_name("receiver")
                        line = node.start_point[0] + 1
                        if method_node.type == "super":
                            # WI-misir: ``super(...)`` calls the PARENT's method
                            # of the enclosing method's name.
                            edges.append(_super_edge(
                                current_method,
                                enclosing_declared_symbol(
                                    node, decl_index, _SUPER_OWNER_NODES,
                                ),
                                superclass_by_qualified, line, run_id,
                            ))
                        elif receiver_node is None or receiver_node.type == "self":
                            # A send to ``self``: ``self.helper(...)``, or a
                            # receiverless ``helper(x)`` / ``helper { }``. That
                            # form is a send even when a local of the name is
                            # in scope (``x = 1; x(2)`` raises NoMethodError);
                            # the ambiguous form, a bare ``identifier``, is the
                            # identifier arm's. ``super(...)`` is excluded by
                            # node type: it is the keyword, not a method.
                            _bare_send(
                                callee_name, current_method, local_symbols,
                                line, edges, run_id, bare=False,
                                shared_keys=shared_keys,
                            )
                        elif _try_receiver_call(
                            receiver_node, callee_name, source,
                            current_method, global_symbols,
                            line, edges, run_id,
                            constant_ancestors=constant_ancestors,
                            shared_keys=shared_keys,
                        ):
                            pass  # A constant receiver, or a construction on one
                        else:
                            # A variable or expression receiver. Typed only by
                            # a construction it was assigned from in the same
                            # method (``c = Conn.new``; ``c.open``).
                            receiver_text = node_text(receiver_node, source)
                            inferred_class = var_types.at(receiver_node).get(receiver_text)
                            if inferred_class is not None:
                                _typed_send(
                                    inferred_class, callee_name, receiver_node,
                                    source, current_method, global_symbols,
                                    constant_ancestors, shared_keys, line,
                                    edges, run_id,
                                )
                            else:
                                # WI-misir: a receiver of unknown type still
                                # CALLS something: reported with ``call_construct:
                                # method`` and nothing else, as javascript
                                # (WI-nasuf), python, java and go do.
                                edges.append(_ruby_stub(
                                    current_method, callee_name, line, run_id,
                                    call_construct="method",
                                ))

        # A bare ``super`` (no parentheses) is its own node; the
        # parenthesised form is a ``call`` whose method is ``super`` (above;
        # WI-misir).
        elif node.type == "super":
            parent = node.parent
            if parent is not None and parent.type == "call" and (
                parent.child_by_field_name("method") == node
            ):
                continue
            current_method = _get_enclosing_method(node, source, decl_index)
            if current_method is not None:
                edges.append(_super_edge(
                    current_method,
                    enclosing_declared_symbol(node, decl_index, _SUPER_OWNER_NODES),
                    superclass_by_qualified, node.start_point[0] + 1, run_id,
                ))

        # Detect bare method calls (identifier nodes that are method names)
        # Skip identifiers that are part of a call/method_call - those are handled above
        elif node.type == "identifier":
            # A call's METHOD name is the call arm's. Its RECEIVER and an
            # element reference's OBJECT are bare identifiers like any other
            # (``current_user.name``, ``params[:id]``; WI-misir), as is
            # nothing that names a definition (``def name``, ``alias``).
            parent = node.parent
            if parent is not None and (
                (parent.type in ("call", "method_call", "scope_resolution")
                 and not (parent.type == "call"
                          and parent.child_by_field_name("receiver") == node))
                or (parent.type == "element_reference"
                    and parent.child_by_field_name("object") != node)
                or parent.type in ("method", "singleton_method", "alias", "undef")
            ):
                continue
            current_method = _get_enclosing_method(node, source, decl_index)
            # A LOCAL (a parameter, block parameter, rescue variable or an
            # earlier assignment in the scope) is a read, not a send, so it
            # never binds a same-named method (WI-misir).
            if current_method is not None and not _is_ruby_local(
                node, source, local_scope_cache,
            ):
                _bare_send(
                    node_text(node, source), current_method, local_symbols,
                    node.start_point[0] + 1, edges, run_id, bare=True,
                    shared_keys=shared_keys,
                )

        # Hash literal function references: {on_success: my_callback}
        # AST: pair → hash_key_symbol : identifier
        # When the value is a bare identifier that resolves to a method,
        # create a references edge. Common in Ruby callback patterns.
        elif node.type == "pair":
            value_node = None
            seen_colon = False
            for pair_child in node.children:
                if pair_child.type == ":":
                    seen_colon = True
                elif seen_colon and pair_child.type == "identifier":
                    value_node = pair_child
                    break

            if value_node is not None:
                ref_name = node_text(value_node, source)
                target = local_symbols.get(ref_name) or global_symbols.get(ref_name)
                if target is None:  # pragma: no cover - defensive resolver fallback
                    lookup_result = resolver.lookup(ref_name, caller_path=_caller_path)
                    if lookup_result.found and lookup_result.symbol is not None:
                        target = lookup_result.symbol
                if target is not None and target.kind == "method":
                    current_method = _get_enclosing_method(
                        node, source, decl_index,
                    )
                    if current_method is not None and target.id != current_method.id:
                        edges.append(Edge.create(
                            src=current_method.id,
                            dst=target.id,
                            edge_type="references",
                            line=node.start_point[0] + 1,
                            evidence_type="hash_field_reference",
                            origin=PASS_ID,
                            origin_run_id=run_id,
                        ))

    return edges


def _resolve_base_class_ruby(
    base_name: str,
    child_sym: Symbol,
    class_by_name: dict[str, list[Symbol]],
    sym_file_require_hints: dict[str, dict[str, str]],
) -> Symbol | None:
    """Resolve a base class name to a specific Symbol, disambiguating collisions.

    ``base_name`` is a full lexical path, the key of ``class_by_name``. When
    one path is declared several times (e.g., test stubs named 'Model'),
    uses a priority cascade:

    1. Same-file match: base class defined in the same file as the child
    2. Require-hint match: child's file has ``require_relative 'path/to/model'``
       and the inferred PascalCase class name matches the base_name; then match
       the require path against candidate file paths
    3. First by ID: deterministic fallback (sorted by symbol ID)

    Args:
        base_name: The base class path to resolve (e.g., 'Model', 'Stub::Model')
        child_sym: The child class symbol (for file context)
        class_by_name: Multi-value lookup: class path -> list of Symbol candidates
        sym_file_require_hints: Maps symbol ID -> file-level require_hints dict
            (PascalCase class name -> require path)

    Returns:
        The resolved base class Symbol, or None if no match found.
    """
    candidates = class_by_name.get(base_name)
    if not candidates:
        return None
    hint_name = base_name.rsplit("::", 1)[-1]

    if len(candidates) == 1:
        return candidates[0]

    child_path = child_sym.path or ""

    # 1. Same-file match: prefer base class in the same file
    same_file = [c for c in candidates if c.path == child_path]
    if len(same_file) == 1:
        return same_file[0]

    # 2. Require-hint match: check if child's file requires match a candidate
    require_hints = sym_file_require_hints.get(child_sym.id, {})
    if hint_name in require_hints:
        require_path = require_hints[hint_name]
        # Match require path against candidate file paths
        # e.g., require_path="models/model" matches candidate path "models/model.rb"
        for cand in candidates:
            cand_path = cand.path or ""
            # Strip .rb extension for comparison
            cand_no_ext = cand_path.rsplit(".rb", 1)[0]
            if cand_no_ext.endswith(require_path):
                return cand

    # 3. Deterministic fallback: first by symbol ID (sorted for stability)
    candidates_sorted = sorted(candidates, key=lambda c: c.id)
    return candidates_sorted[0]


def _extract_inheritance_edges(
    symbols: list[Symbol],
    class_by_name: dict[str, list[Symbol]],
    sym_file_require_hints: dict[str, dict[str, str]],
    run_id: str,
) -> list[Edge]:
    """Extract extends edges from class inheritance (META-001).

    For each class with base_classes metadata, creates extends edges to
    base classes that exist in the analyzed codebase. This enables the
    type hierarchy linker to create dispatches_to edges for polymorphic dispatch.

    The superclass is looked up as Ruby looks a constant up: under each
    lexical scope the class is declared in, innermost first
    (:func:`_declaration_nesting`), then at the top level; a name written with
    a root ``::`` at the top level only. Only a full path matches, so
    ``< ActiveRecord::Base`` does not reach a project class that merely ends
    in ``Base``, and ``< A::B::X`` does not reach ``A::X`` (INV-dosoz). The
    class's own path is skipped: ``module Api; class Base < Base`` names the
    outer ``Base``.

    When one path is declared several times (common in large repos with test stubs),
    uses require-hint-aware disambiguation via ``_resolve_base_class_ruby()`` to find
    the correct target.

    Args:
        symbols: All extracted symbols
        class_by_name: Multi-value lookup: class path (:func:`_registry_key`)
            -> list of Symbol candidates
        sym_file_require_hints: Maps symbol ID -> file-level require_hints dict
        run: Current analysis run for provenance

    Returns:
        List of extends edges for inheritance relationships
    """
    edges: list[Edge] = []

    for sym in symbols:
        if sym.kind != "class":
            continue

        base_classes = sym.meta.get("base_classes", []) if sym.meta else []
        if not base_classes:
            continue

        for base_class_name in base_classes:
            own_path = _registry_key(sym)
            base_sym = None
            for candidate in _constant_candidates(
                base_class_name, _declaration_nesting(sym),
            ):
                if candidate == own_path:
                    continue
                base_sym = _resolve_base_class_ruby(
                    candidate, sym, class_by_name, sym_file_require_hints
                )
                if base_sym is not None:
                    break
            if base_sym is None:
                continue

            edge = Edge.create(
                src=sym.id,
                dst=base_sym.id,
                edge_type="extends",
                line=sym.span.start_line if sym.span else 0,
                origin=PASS_ID,
                origin_run_id=run_id,
                evidence_type="ast_extends",
            )
            edges.append(edge)

    return edges


def _ruby_import_scope() -> ImportScope:
    """The table of one Ruby file (ADR-0064): EMPTY, of namespaces.

    Ruby finds a constant by its name from the place it is written, the
    enclosing modules innermost out (``ImportScope.own_namespace`` stays
    ``None``: every enclosing module is a scope, as in C#). ``require`` and
    ``require_relative`` load a file and bind no name, so nothing is recorded
    for them; ``include M`` / ``extend M`` make ``M``'s methods the class's
    own, which ``included_modules`` and the ``includes`` edges carry, not a
    binding of the file. The table's presence says that this analyzer has
    adopted the shared pass.

    ``library_names_screened`` stays False, so the single-holder name linker
    binds no Ruby call, although every stub whose name Ruby's core or Rails
    defines goes out with ``stdlib_member_name``
    (:data:`_RUBY_LIBRARY_METHOD_NAMES`). Measured with it on (batch 5b of
    ADR-0064, three trees, postal and two of mastodon's): of 15 calls the
    linker bound, read in the source, 7 were right. The wrong ones are calls
    on a model's column or association (``follow.target_account``,
    ``e.server.hostname``, ``domain_block.domain``), which a Rails model
    answers from its database schema and ``belongs_to``, bound to the one
    repository class that declares a method of that name, and a gem's method
    (OpenSSL's ``public_key``). No list of a library's names holds an
    application's columns, and no stub fact says what a receiver is.
    """
    return ImportScope(library_names_screened=False)


class RubyAnalyzer(TreeSitterAnalyzer):
    """Ruby language analyzer using tree-sitter-ruby.

    Overrides ``analyze()`` because Ruby requires multiple custom passes
    beyond the standard two-pass architecture:
    - Pass 1: Symbol extraction (standard)
    - Pass 2: Call/import edge extraction (standard)
    - Pass 2b: Rails callback edges (before_action, after_action, etc.)
    - Pass 2c: ActiveRecord association edges (has_many, belongs_to, etc.)
    - Pass 2d: Ruby delegate edges (delegate :method, to: :association)
    - Pass 3: Route extraction with test-file filtering
    - Post: Inheritance edges with require-hint disambiguation
    """

    lang = "ruby"
    file_patterns: ClassVar[list[str]] = ["*.rb"]
    grammar_module = "tree_sitter_ruby"

    def register_symbol(
        self, symbol: Symbol, global_symbols: dict,
    ) -> None:
        """Register symbol under its full lexical path (:func:`_registry_key`).

        ``A::B::X`` / ``A::B::X#go`` for a declaration nested in blocks as for
        a compact one, so same-named declarations in different namespaces
        never share a key (INV-dosoz). The ``NameResolver`` suffix index
        handles short-name lookups across ``#`` and ``.`` separators.

        WI-jusus chokepoint: field/variable data anchors (constants, attr_*
        attributes, class variables) are kept OUT of the call-resolution
        registry — a data anchor is never a call target, so registering it would
        let it clobber or suffix-shadow a same-named method. They remain in
        ``analysis.symbols`` (search / centrality) since the output set is built
        independently of this registry.
        """
        if symbol.kind in ("field", "variable"):
            return
        global_symbols[_registry_key(symbol)] = symbol

    def analyze(
        self,
        repo_root: Path,
        max_files: int | None = None,
    ) -> AnalysisResult:
        """Run multi-pass Ruby analysis.

        Overrides the base ``analyze()`` to add Rails callback, ActiveRecord
        association, delegate, and route extraction passes.
        """
        start_time = time.time()
        run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

        # 1. Check grammar availability
        if not self._check_grammar_available():
            import warnings

            warnings.warn(
                f"{self.lang} analysis skipped: grammar not available. "
                f"Install the required tree-sitter grammar package.",
                UserWarning,
                stacklevel=2,
            )
            run.duration_ms = int((time.time() - start_time) * 1000)
            return AnalysisResult(
                run=run,
                skipped=True,
                skip_reason=f"{self.lang} tree-sitter grammar not available",
                skip_reason_code=DEPENDENCY_UNAVAILABLE,
            )

        # 2. Initialize parser
        try:
            parser = self._create_parser()
        except Exception as e:
            run.duration_ms = int((time.time() - start_time) * 1000)
            return AnalysisResult(
                run=run,
                skipped=True,
                skip_reason=f"Failed to load Ruby parser: {e}",
                skip_reason_code=PASS_CRASHED,
            )

        # Pass 1: Extract all symbols from all files
        file_analyses: dict[Path, FileAnalysis] = {}
        all_rb_files: list[Path] = list(find_ruby_files(repo_root))
        files_skipped = 0

        for rb_file in all_rb_files:
            try:
                source = rb_file.read_bytes()
            except OSError as e:  # pragma: no cover - IO errors hard to trigger in tests
                files_skipped += 1
                run.record_failed_file(
                    str(rb_file.relative_to(repo_root)),
                    f"{type(e).__name__}: {e}",
                )
                continue
            tree = parser.parse(source)
            rel_path = str(rb_file.relative_to(repo_root))
            record_partial_parse(run, rel_path, tree)  # WI-bulaz
            analysis = _extract_symbols_from_file(
                tree, source, rel_path, run.execution_id,
            )
            populate_docstrings_from_tree(tree.root_node, source, analysis.symbols)
            if analysis.symbols:
                file_analyses[rb_file] = analysis
            else:
                files_skipped += 1

        # Build the global symbol registry, and the method keys two files
        # declare (``_declared_once``).
        global_symbols: dict[str, Symbol] = {}
        files_declaring: dict[str, set[str]] = {}
        for analysis in file_analyses.values():
            for symbol in analysis.symbols:
                self.register_symbol(symbol, global_symbols)
                if symbol.kind == "method":
                    files_declaring.setdefault(_registry_key(symbol), set()).add(symbol.path)
        shared_keys = frozenset(
            key for key, paths in files_declaring.items() if len(paths) > 1
        )

        # A reopened class's superclass, by its QUALIFIED name (WI-gikuk).
        superclass_by_qualified = superclasses_by_qualified_name(
            sym for analysis in file_analyses.values() for sym in analysis.symbols
        )
        # The ancestors a constant lookup walks after the lexical scopes.
        constant_ancestors = _constant_ancestors(
            (sym for analysis in file_analyses.values() for sym in analysis.symbols),
            global_symbols,
        )

        # Pass 2: Extract edges from files with symbols
        resolver = NameResolver(global_symbols)
        all_symbols: list[Symbol] = []
        all_edges: list[Edge] = []
        all_usage_contexts: list[UsageContext] = []

        for rb_file, analysis in file_analyses.items():
            all_symbols.extend(analysis.symbols)

            source = rb_file.read_bytes()
            tree = parser.parse(source)
            rel_path = str(rb_file.relative_to(repo_root))

            edges = _extract_edges_from_file(
                tree, source, rel_path,
                analysis.symbol_by_name, global_symbols,
                run.execution_id, resolver,
                file_symbols=analysis.symbols,
                superclass_by_qualified=superclass_by_qualified,
                constant_ancestors=constant_ancestors,
                abs_path=rb_file,
                shared_keys=shared_keys,
            )
            # ADR-0015 Tier 1: annotate edges with dataflow access modes
            _ruby_df = _get_dataflow_config("ruby")
            if _ruby_df is not None:
                edges = _annotate_dataflow(edges, tree, source, _ruby_df)
            all_edges.extend(edges)

        # Pass 2b: Extract Rails callback edges (before_action, after_action, etc.)
        # These create dispatches_to edges from controller class -> callback method.
        for rb_file, analysis in file_analyses.items():
            try:
                source = rb_file.read_bytes()
                tree = parser.parse(source)
            except (OSError, IOError):  # pragma: no cover
                continue
            callback_edges = _extract_rails_callbacks(
                tree, source, rb_file, analysis.symbol_by_name, global_symbols,
                resolver, run.execution_id,
            )
            all_edges.extend(callback_edges)
            # Calls that hand a framework a local method or class (WI-movur).
            all_usage_contexts.extend(_extract_registration_contexts(
                tree, source, str(rb_file.relative_to(repo_root)),
                global_symbols, constant_ancestors,
            ))

        # Pass 2c: Extract ActiveRecord association edges (has_many, belongs_to, etc.)
        # These create association edges from model class -> target model class.
        for rb_file, analysis in file_analyses.items():
            try:
                source = rb_file.read_bytes()
                tree = parser.parse(source)
            except (OSError, IOError):  # pragma: no cover
                continue
            assoc_edges = _extract_activerecord_associations(
                tree, source, rb_file, analysis.symbol_by_name, global_symbols,
                run.execution_id,
            )
            all_edges.extend(assoc_edges)

        # Pass 2d: Extract Ruby delegate edges (delegate :method, to: :association)
        # These create references edges (meta framework_dispatch
        # ``ruby_delegate``) from class -> target method.
        for rb_file, analysis in file_analyses.items():
            try:
                source = rb_file.read_bytes()
                tree = parser.parse(source)
            except (OSError, IOError):  # pragma: no cover
                continue
            delegate_edges = _extract_ruby_delegates(
                tree, source, rb_file, analysis.symbol_by_name, global_symbols,
                run.execution_id,
            )
            all_edges.extend(delegate_edges)

        # Pass 3: Extract usage contexts and route symbols with file-path filtering.
        # Test files (spec/, test/, etc.) use get/post/delete as HTTP test helpers,
        # NOT route definitions. Only detect routes in:
        # 1. Route definition files (routes.rb, config/routes/*.rb)
        # 2. Non-test app files with Sinatra-style do-block routes
        for rb_file in all_rb_files:
            is_test = _is_test_directory(rb_file, repo_root)
            if is_test:
                continue  # Skip test files entirely -- HTTP methods are test helpers
            try:
                source = rb_file.read_bytes()
                tree = parser.parse(source)
                symbol_by_name = file_analyses.get(rb_file, FileAnalysis()).symbol_by_name
                usage_contexts, route_symbols = _extract_rails_routes(
                    tree.root_node, source, rb_file, symbol_by_name, run.execution_id
                )
                if _is_route_definition_file(rb_file, repo_root):
                    # Route definition files: include all detected routes
                    all_usage_contexts.extend(usage_contexts)
                    all_symbols.extend(route_symbols)
                else:
                    # Non-route app files: only Sinatra-style routes (with do blocks)
                    # Bare get/post calls without blocks are likely HTTP client calls
                    for ctx in usage_contexts:
                        if ctx.metadata.get("has_block"):
                            all_usage_contexts.append(ctx)
                    for sym in route_symbols:
                        if sym.meta.get("has_block"):
                            all_symbols.append(sym)
            except (OSError, IOError):  # pragma: no cover
                pass

        run.files_analyzed = len(file_analyses)
        run.files_skipped = files_skipped
        run.duration_ms = int((time.time() - start_time) * 1000)

        # Extract inheritance edges (META-001: base_classes metadata -> extends edges)
        # Build multi-value class lookup for disambiguation (INV-015)
        # keyed by full lexical path (INV-dosoz)
        class_by_name: dict[str, list[Symbol]] = {}
        for s in all_symbols:
            if s.kind == "class":
                class_by_name.setdefault(_registry_key(s), []).append(s)
        # Build per-symbol require_hints mapping for import disambiguation
        sym_file_require_hints: dict[str, dict[str, str]] = {}
        for _rb_file, analysis in file_analyses.items():
            for sym in analysis.symbols:
                sym_file_require_hints[sym.id] = analysis.import_aliases
        inheritance_edges = _extract_inheritance_edges(
            all_symbols, class_by_name, sym_file_require_hints, run.execution_id,
        )
        all_edges.extend(inheritance_edges)

        return AnalysisResult(
            symbols=all_symbols,
            edges=all_edges,
            usage_contexts=all_usage_contexts,
            run=run,
            import_scopes={
                str(rb_file.relative_to(repo_root)): _ruby_import_scope()
                for rb_file in all_rb_files
            },
        )


_analyzer = RubyAnalyzer()


@register_analyzer("ruby")
def analyze_ruby(repo_root: Path) -> AnalysisResult:
    """Analyze all Ruby files in a repository."""
    return _analyzer.analyze(repo_root)
