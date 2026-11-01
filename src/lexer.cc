#include "lexer.h"

#include <charconv>
#include <system_error>
#include <utility>

namespace {

bool is_digit(char character) {
    return character >= '0' && character <= '9';
}

bool identifier_start(char character) {
    return (character >= 'a' && character <= 'z') ||
           (character >= 'A' && character <= 'Z') || character == '_';
}

bool identifier_part(char character) {
    return identifier_start(character) || is_digit(character);
}

TokenKind word_kind(std::string word) {
    for (char &character : word) {
        if (character >= 'a' && character <= 'z') {
            character = static_cast<char>(character - 'a' + 'A');
        }
    }
    if (word == "SELECT") return TokenKind::Select;
    if (word == "DISTINCT") return TokenKind::Distinct;
    if (word == "FROM") return TokenKind::From;
    if (word == "AS") return TokenKind::As;
    if (word == "WHERE") return TokenKind::Where;
    if (word == "AND") return TokenKind::And;
    return TokenKind::Identifier;
}

} // namespace

LexerError::LexerError(SourceLocation location, const std::string &message)
    : std::invalid_argument{"line " + std::to_string(location.line) + ", column " +
                            std::to_string(location.column) + ": " + message},
      location_{location} {}

const SourceLocation &LexerError::location() const noexcept {
    return location_;
}

Lexer::Lexer(std::string input) : input_{std::move(input)} {}

bool Lexer::at_end() const {
    return position_.offset == input_.size();
}

char Lexer::peek(std::size_t lookahead) const {
    if (lookahead >= input_.size() - position_.offset) {
        return '\0';
    }
    return input_[position_.offset + lookahead];
}

char Lexer::advance() {
    const char character = input_[position_.offset];
    if (character == '\0') {
        throw LexerError{position_, "NUL characters are not allowed in SQLP text"};
    }
    ++position_.offset;
    if (character == '\r') {
        ++position_.line;
        position_.column = 1;
    } else if (character == '\n') {
        if (position_.offset < 2 || input_[position_.offset - 2] != '\r') {
            ++position_.line;
        }
        position_.column = 1;
    } else {
        ++position_.column;
    }
    return character;
}

void Lexer::skip_ignored() {
    while (!at_end()) {
        const char character = peek();
        if (character == ' ' || character == '\t' || character == '\n' ||
            character == '\r' || character == '\f' || character == '\v') {
            advance();
        } else if (character == '-' && peek(1) == '-') {
            while (!at_end() && peek() != '\n' && peek() != '\r') {
                advance();
            }
        } else if (character == '/' && peek(1) == '*') {
            const auto start = position_;
            advance();
            advance();
            while (!at_end() && !(peek() == '*' && peek(1) == '/')) {
                advance();
            }
            if (at_end()) {
                throw LexerError{start, "Unterminated block comment"};
            }
            advance();
            advance();
        } else {
            return;
        }
    }
}

Token Lexer::quoted(char delimiter, TokenKind kind) {
    const auto start = position_;
    advance();
    std::string text;
    while (!at_end()) {
        const char character = advance();
        if (character == delimiter) {
            if (!at_end() && peek() == delimiter) {
                advance();
                text += delimiter;
            } else {
                if (kind == TokenKind::Identifier && text.empty()) {
                    throw LexerError{start, "A quoted identifier cannot be empty"};
                }
                return {kind, std::move(text), start, position_, std::nullopt};
            }
        } else {
            text += character;
        }
    }
    throw LexerError{start, kind == TokenKind::String ? "Unterminated string literal" :
                                                       "Unterminated quoted identifier"};
}

Token Lexer::integer() {
    const auto start = position_;
    if (peek() == '-') {
        advance();
    }
    while (!at_end() && is_digit(peek())) {
        advance();
    }
    if (!at_end() && (identifier_part(peek()) || peek() == '.')) {
        throw LexerError{start, "Only decimal int64 literals are supported"};
    }

    const auto text = input_.substr(start.offset, position_.offset - start.offset);
    std::int64_t value = 0;
    const auto parsed = std::from_chars(text.data(), text.data() + text.size(), value);
    if (parsed.ec != std::errc{} || parsed.ptr != text.data() + text.size()) {
        throw LexerError{start, "Integer literal is outside the int64 range"};
    }
    return {TokenKind::Integer, text, start, position_, value};
}

Token Lexer::next() {
    skip_ignored();
    const auto start = position_;
    if (at_end()) {
        return {TokenKind::End, "", start, start, std::nullopt};
    }

    const char character = peek();
    if (identifier_start(character)) {
        while (!at_end() && identifier_part(peek())) {
            advance();
        }
        const auto text = input_.substr(start.offset, position_.offset - start.offset);
        return {word_kind(text), text, start, position_, std::nullopt};
    }
    if (is_digit(character) || (character == '-' && is_digit(peek(1)))) {
        return integer();
    }
    if (character == '\'') return quoted('\'', TokenKind::String);
    if (character == '"') return quoted('"', TokenKind::Identifier);

    TokenKind kind;
    switch (character) {
    case '.': kind = TokenKind::Dot; break;
    case ',': kind = TokenKind::Comma; break;
    case '=': kind = TokenKind::Equal; break;
    case ';': kind = TokenKind::Semicolon; break;
    default:
        throw LexerError{start, character == '\0' ? "NUL characters are not allowed in SQLP text" :
                                                   "Unexpected character in SQLP text"};
    }
    advance();
    return {kind, std::string(1, character), start, position_, std::nullopt};
}
