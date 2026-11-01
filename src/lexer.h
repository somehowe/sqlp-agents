#ifndef SQLP_LEXER_H
#define SQLP_LEXER_H

#include <cstddef>
#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string>

enum class TokenKind {
    Select, Distinct, From, As, Where, And,
    Identifier, String, Integer,
    Dot, Comma, Equal, Semicolon, End
};

// Offsets start at zero; lines and columns start at one. Columns count bytes
// (a tab counts as one). CRLF and a lone CR both count as one line break.
struct SourceLocation {
    std::size_t offset = 0;
    std::size_t line = 1;
    std::size_t column = 1;
    bool operator==(const SourceLocation &) const = default;
};

struct Token {
    TokenKind kind;
    // Quoted identifiers/strings are decoded; other tokens preserve spelling.
    std::string text;
    SourceLocation location;
    SourceLocation end; // Exclusive end of the original source spelling.
    std::optional<std::int64_t> integer_value;
};

class LexerError : public std::invalid_argument {
    SourceLocation location_;

public:
    LexerError(SourceLocation location, const std::string &message);
    const SourceLocation &location() const noexcept;
};

// Owns its input. Returned tokens also own their text, so neither borrows
// strings from the caller or from the lexer's changing position.
class Lexer {
    std::string input_;
    SourceLocation position_;

    bool at_end() const;
    char peek(std::size_t lookahead = 0) const;
    char advance();
    void skip_ignored();
    Token quoted(char delimiter, TokenKind kind);
    Token integer();

public:
    explicit Lexer(std::string input);
    Token next();
};

#endif
