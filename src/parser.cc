#include "parser.h"

#include "sqlp_parser.h"

#include <optional>
#include <utility>

ParseError::ParseError(SourceLocation location, const std::string &message)
    : std::invalid_argument{"line " + std::to_string(location.line) + ", column " +
                            std::to_string(location.column) + ": " + message},
      location_{location} {}

const SourceLocation &ParseError::location() const noexcept {
    return location_;
}

sqlp::detail::BisonParser::symbol_type sqlp::detail::yylex(Lexer &lexer) {
    auto token = lexer.next();
    const SourceSpan span{token.location, token.end};
    using Parser = BisonParser;
    switch (token.kind) {
    case TokenKind::Select: return Parser::make_SELECT(span);
    case TokenKind::Distinct: return Parser::make_DISTINCT(span);
    case TokenKind::From: return Parser::make_FROM(span);
    case TokenKind::As: return Parser::make_AS(span);
    case TokenKind::Where: return Parser::make_WHERE(span);
    case TokenKind::And: return Parser::make_AND(span);
    case TokenKind::Identifier: return Parser::make_IDENTIFIER(std::move(token.text), span);
    case TokenKind::String: return Parser::make_STRING(std::move(token.text), span);
    case TokenKind::Integer: return Parser::make_INTEGER(*token.integer_value, span);
    case TokenKind::Dot: return Parser::make_DOT(span);
    case TokenKind::Comma: return Parser::make_COMMA(span);
    case TokenKind::Equal: return Parser::make_EQUAL(span);
    case TokenKind::Semicolon: return Parser::make_SEMICOLON(span);
    case TokenKind::End: return Parser::make_END(span);
    }
    throw std::logic_error{"Unknown lexer token kind"};
}

void sqlp::detail::BisonParser::error(const location_type &span, const std::string &message) {
    throw ParseError{span.begin, message};
}

Query parse_query(std::string source) {
    Lexer lexer{std::move(source)};
    std::optional<Query> result;
    sqlp::detail::BisonParser parser{lexer, result};
    if (parser.parse() != 0 || !result) {
        throw std::logic_error{"Parser completed without a query"};
    }
    return std::move(*result);
}

CompiledQuery compile_sqlp(std::string source, const Catalog &catalog) {
    return compile_query(parse_query(std::move(source)), catalog);
}
