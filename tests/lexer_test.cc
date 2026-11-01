#include "lexer.h"

#include <iostream>
#include <limits>
#include <utility>
#include <vector>

namespace {

void require(bool condition, const std::string &message) {
    if (!condition) {
        throw std::runtime_error{message};
    }
}

std::vector<Token> tokenize(std::string input) {
    Lexer lexer{std::move(input)};
    std::vector<Token> tokens;
    do {
        tokens.push_back(lexer.next());
    } while (tokens.back().kind != TokenKind::End);
    return tokens;
}

void require_error(const std::string &input, SourceLocation location, const std::string &message) {
    try {
        tokenize(input);
    } catch (const LexerError &error) {
        require(error.location() == location, "Incorrect diagnostic location: " + message);
        require(std::string{error.what()}.find("line " + std::to_string(location.line) +
                    ", column " + std::to_string(location.column)) != std::string::npos,
                "Diagnostic omitted line/column: " + message);
        return;
    }
    throw std::runtime_error{"Accepted invalid input: " + message};
}

} // namespace

int main() {
    try {
        const auto query = tokenize(
            "sElEcT DISTINCT w.author.name AS author_name FROM WROTE w "
            "WHERE w.paper.title = 'Databases';");
        const std::vector<TokenKind> expected{
            TokenKind::Select, TokenKind::Distinct, TokenKind::Identifier, TokenKind::Dot,
            TokenKind::Identifier, TokenKind::Dot, TokenKind::Identifier, TokenKind::As,
            TokenKind::Identifier, TokenKind::From, TokenKind::Identifier, TokenKind::Identifier,
            TokenKind::Where, TokenKind::Identifier, TokenKind::Dot, TokenKind::Identifier,
            TokenKind::Dot, TokenKind::Identifier, TokenKind::Equal, TokenKind::String,
            TokenKind::Semicolon, TokenKind::End
        };
        require(query.size() == expected.size(), "Wrong number of tokens for example query");
        for (std::size_t index = 0; index < expected.size(); ++index) {
            require(query[index].kind == expected[index], "Unexpected token in example query");
        }
        require(query[0].text == "sElEcT" && query[10].text == "WROTE" &&
                    query[19].text == "Databases", "Token spelling/value was lost");

        const auto words = tokenize("select SELECTOR _id a1 aNd \"SELECT\" ,");
        require(words[0].kind == TokenKind::Select && words[1].kind == TokenKind::Identifier &&
                    words[2].text == "_id" && words[3].text == "a1" &&
                    words[4].kind == TokenKind::And && words[5].kind == TokenKind::Identifier &&
                    words[5].text == "SELECT" && words[6].kind == TokenKind::Comma,
                "Keywords were confused with identifiers");

        const auto quoted = tokenize("'O''Brien' 'SELECT -- /*' '' \"a\"\"b\" 'back\\slash'");
        require(quoted[0].text == "O'Brien" && quoted[1].text == "SELECT -- /*" &&
                    quoted[2].text.empty() && quoted[3].text == "a\"b" &&
                    quoted[4].text == "back\\slash", "Quoted text was decoded incorrectly");
        const auto multiline = tokenize("'a\nb' x");
        require(multiline[0].text == "a\nb" && multiline[0].end == SourceLocation{5, 2, 3} &&
                    multiline[1].location == SourceLocation{6, 2, 4},
                "Multiline literal locations were incorrect");

        const auto numbers = tokenize("0 -1 9223372036854775807 -9223372036854775808 007");
        require(numbers[0].integer_value == 0 && numbers[1].integer_value == -1 &&
                    numbers[2].integer_value == std::numeric_limits<std::int64_t>::max() &&
                    numbers[3].integer_value == std::numeric_limits<std::int64_t>::min() &&
                    numbers[4].integer_value == 7 && numbers[4].text == "007",
                "Signed integer values were parsed incorrectly");
        require(!query[0].integer_value && !quoted[0].integer_value,
                "Non-integer token unexpectedly carried an integer value");

        const auto comments = tokenize("-- ignored\r\nSELECT /* a\ncomment */ 'not -- a comment';");
        require(comments.size() == 4 && comments[0].kind == TokenKind::Select &&
                    comments[1].text == "not -- a comment" &&
                    comments[2].kind == TokenKind::Semicolon,
                "Comments affected token contents");
        require(tokenize("--1\n-2")[0].integer_value == -2,
                "Line comment was mistaken for a negative number");
        require(tokenize("/* empty */ -- final comment").size() == 1,
                "Comment-only input did not end cleanly");

        Lexer positions{" \tSELECT\r\nw.author"};
        const auto select = positions.next();
        const auto root = positions.next();
        const auto dot = positions.next();
        const auto feature = positions.next();
        const auto end = positions.next();
        require(select.location == SourceLocation{2, 1, 3} && select.end == SourceLocation{8, 1, 9} &&
                    root.location == SourceLocation{10, 2, 1} &&
                    dot.location == SourceLocation{11, 2, 2} &&
                    feature.location == SourceLocation{12, 2, 3} &&
                    end.location == SourceLocation{18, 2, 9}, "Source positions were incorrect");
        require(positions.next().kind == TokenKind::End && positions.next().location == end.location,
                "Repeated EOF changed the lexer position");
        require(tokenize("\rx")[0].location == SourceLocation{1, 2, 1},
                "Lone CR was not treated as a newline");
        require(tokenize("")[0].location == SourceLocation{0, 1, 1},
                "Empty input had the wrong EOF location");

        std::string original{"owned_name"};
        Lexer owned{original};
        original = "changed";
        require(owned.next().text == "owned_name", "Lexer borrowed mutable caller input");

        for (const auto &invalid : std::vector<std::string>{
                 "9223372036854775808", "-9223372036854775809", std::string(1000, '9'),
                 "12abc", "1.5", "1e3", "0x10", "+1", "-", "@", "!"}) {
            require_error(invalid, {0, 1, 1}, invalid);
        }
        require_error(" \n 'unfinished", {3, 2, 2}, "unterminated string");
        require_error(" \"unfinished", {1, 1, 2}, "unterminated identifier");
        require_error("\n/* unfinished", {1, 2, 1}, "unterminated comment");
        require_error("\"\"", {0, 1, 1}, "empty quoted identifier");
        require_error(std::string{"x\0tail", 6}, {1, 1, 2}, "embedded NUL");
        require_error(std::string{"'a\0b'", 5}, {2, 1, 3}, "NUL inside string");
        require_error(std::string{"--\0", 3}, {2, 1, 3}, "NUL inside comment");

        std::cout << "lexer checks passed\n";
    } catch (const std::exception &error) {
        std::cerr << "lexer check failed: " << error.what() << '\n';
        return 1;
    }
}
