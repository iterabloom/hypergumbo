%% Minimum reproducer for Edge.meta.evidence_type == "macro_expansion".
%%
%% INV-zihor: tree-sitter parses erlang SOURCE, so ?LOG_* never expands and
%% the logging call it stands for produced no edge at all. The analyzer now
%% expands OTP's eight kernel level macros, and stamps `macro_expansion`
%% rather than `ast_call` because nothing in the AST was ever a call here.
%%
%% The include is LOAD-BEARING, not decoration: the expansion is gated on the
%% file including a header named logger.hrl, so deleting this line silently
%% removes the coverage this fixture exists to provide.
-module(logger_macro).
-include_lib("kernel/include/logger.hrl").
-export([emit/1]).

emit(Value) ->
    ?LOG_WARNING("value ~tp", [Value]),
    ok.
