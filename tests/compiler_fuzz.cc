#include "parser.h"

#include <charconv>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

namespace {

std::uint64_t number(std::string_view text) {
    std::uint64_t value = 0;
    const auto result = std::from_chars(text.data(), text.data() + text.size(), value);
    if (result.ec != std::errc{} || result.ptr != text.data() + text.size()) {
        throw std::invalid_argument{"Expected an unsigned decimal integer"};
    }
    return value;
}

std::string hexadecimal(std::string_view text) {
    constexpr char digits[] = "0123456789abcdef";
    std::string result;
    result.reserve(text.size() * 2);
    for (const unsigned char byte : text) {
        result += digits[byte >> 4];
        result += digits[byte & 15];
    }
    return result;
}

} // namespace

int main(int argc, char **argv) {
    std::uint64_t iterations = 100000;
    std::uint64_t seed = 20261101;
    bool saw_iterations = false;
    bool saw_seed = false;
    try {
        for (int index = 1; index < argc; index += 2) {
            const std::string_view option{argv[index]};
            if (index + 1 == argc) throw std::invalid_argument{"Option needs a value"};
            if (option == "--iterations" && !saw_iterations) {
                iterations = number(argv[index + 1]);
                saw_iterations = true;
            } else if (option == "--seed" && !saw_seed) {
                seed = number(argv[index + 1]);
                saw_seed = true;
            } else {
                throw std::invalid_argument{"Unknown or duplicate option"};
            }
        }
        if (iterations == 0) throw std::invalid_argument{"iterations must be positive"};
    } catch (const std::invalid_argument &error) {
        std::cerr << error.what() << "\nUsage: compiler_fuzz [--iterations N] [--seed N]\n";
        return 2;
    }

    const Catalog catalog{
        {{"T", {{"id", ColumnType::Integer, true}, {"fk", ColumnType::Integer, false},
                 {"name", ColumnType::String, false}}}},
        {{"T", "next", "fk", "T", "id"}}
    };
    const std::vector<std::string> seeds{
        "SELECT t.name FROM T t;",
        "SELECT DISTINCT t.next.next.name AS title, t.id FROM T AS t WHERE t.id = -9223372036854775808 AND t.name = 'O''Brien';",
        "SELECT \"t\".\"name\" FROM \"T\" \"t\" WHERE \"t\".\"name\" = ''; -- hi\r\n",
        "SELECT t.next.id FROM T t WHERE t.next.id = 9223372036854775807;"
    };
    const std::string alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789 \t\r\n\f\v.,;='\"-/*+()\\";
    std::mt19937_64 random{seed};
    std::uint64_t accepted = 0;
    std::uint64_t rejected = 0;
    for (std::uint64_t iteration = 0; iteration < iterations; ++iteration) {
        std::string source = seeds[random() % seeds.size()];
        for (std::uint64_t edit = 0, count = random() % 16; edit < count; ++edit) {
            const auto index = random() % (source.size() + 1);
            if (random() % 4 == 0 && index < source.size()) {
                source.erase(index, 1);
            } else {
                const auto byte = random() % 20 == 0 ? static_cast<char>(random() % 256)
                                                     : alphabet[random() % alphabet.size()];
                source.insert(index, 1, byte);
            }
        }
        try {
            Lexer lexer{source};
            std::size_t count = 0;
            while (lexer.next().kind != TokenKind::End) {
                if (++count > source.size() + 1) {
                    throw std::runtime_error{"Lexer exceeded its finite token budget"};
                }
            }
            const auto compiled = compile_sqlp(source, catalog);
            if (compiled.sql.empty()) throw std::runtime_error{"Accepted query has empty SQL"};
            ++accepted;
        } catch (const LexerError &) {
            ++rejected;
        } catch (const ParseError &) {
            ++rejected;
        } catch (const std::invalid_argument &) {
            // compile_query documents invalid_argument for semantic rejection:
            // malformed names, type errors, unsupported references/profile.
            ++rejected;
        } catch (const std::exception &error) {
            std::cerr << "Unexpected exception: " << error.what() << "\nseed=" << seed
                      << " iteration=" << iteration << " source_hex=" << hexadecimal(source) << '\n';
            return 1;
        }
    }
    std::cout << "PASS " << iterations << " mutation cases (seed " << seed << "): "
              << accepted << " accepted, " << rejected << " rejected\n";
}
