#ifndef SQLP_PARSER_H
#define SQLP_PARSER_H

#include "lexer.h"
#include "query.h"

#include <stdexcept>
#include <string>

// Bison combines token spans through these begin/end members.
struct SourceSpan {
    SourceLocation begin;
    SourceLocation end;
};

class ParseError : public std::invalid_argument {
    SourceLocation location_;

public:
    ParseError(SourceLocation location, const std::string &message);
    const SourceLocation &location() const noexcept;
};

// Parse one SELECT statement, including its semicolon and end of input.
// Name/type resolution is left to compile_query; LexerError propagates.
Query parse_query(std::string source);

// Convenience entry point for the currently supported text-to-SQL subset.
CompiledQuery compile_sqlp(std::string source, const Catalog &catalog);

#endif
