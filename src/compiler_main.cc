#include "parser.h"

#include <cstdio>
#include <iostream>
#include <iterator>
#include <optional>
#include <set>
#include <string_view>
#include <utility>

namespace {

constexpr std::string_view help =
    "Usage: sqlpc [catalog options] < query.sqlp\n"
    "  --column TABLE COLUMN integer|string unique|nonunique   (repeatable)\n"
    "  --reference SOURCE_TABLE ROLE SOURCE_COLUMN TARGET_TABLE TARGET_COLUMN\n"
    "                                                        (repeatable)\n"
    "  --help                                                (alone)\n"
    "Reads one SQLP query from stdin and writes one version-1 JSON response.\n"
    "Integer parameter values are decimal strings to preserve all int64 bits.\n";

int finish(int status) {
    std::cout.flush();
    return std::cout && !std::ferror(stdout) ? status : 3;
}

// JSON strings need valid UTF-8, even when the source query is rejected later.
bool valid_utf8(std::string_view text) {
    for (std::size_t i = 0; i < text.size();) {
        const auto first = static_cast<unsigned char>(text[i]);
        if (first < 0x80) { ++i; continue; }
        const std::size_t count = first >= 0xc2 && first <= 0xdf ? 2 :
                                  first >= 0xe0 && first <= 0xef ? 3 :
                                  first >= 0xf0 && first <= 0xf4 ? 4 : 0;
        if (count == 0 || count > text.size() - i) return false;
        for (std::size_t j = 1; j < count; ++j) {
            const auto next = static_cast<unsigned char>(text[i + j]);
            if (next < 0x80 || next > 0xbf) return false;
        }
        const auto second = static_cast<unsigned char>(text[i + 1]);
        if ((first == 0xe0 && second < 0xa0) || (first == 0xed && second >= 0xa0) ||
            (first == 0xf0 && second < 0x90) || (first == 0xf4 && second >= 0x90)) return false;
        i += count;
    }
    return true;
}

void json_string(std::string_view value) {
    constexpr char hex[] = "0123456789abcdef";
    std::cout << '"';
    for (unsigned char character : value) {
        if (character == '"' || character == '\\') {
            std::cout << '\\' << static_cast<char>(character);
        } else if (character < 0x20) {
            std::cout << "\\u00" << hex[character >> 4] << hex[character & 0x0f];
        } else {
            std::cout << static_cast<char>(character);
        }
    }
    std::cout << '"';
}

void error_response(std::string_view stage, const std::string &message,
                    std::optional<SourceLocation> location = std::nullopt) {
    std::cout << "{\"version\":1,\"ok\":false,\"error\":{\"stage\":";
    json_string(stage);
    std::cout << ",\"message\":";
    json_string(message);
    if (location) {
        std::cout << ",\"location\":{\"offset\":" << location->offset
                  << ",\"line\":" << location->line << ",\"column\":" << location->column << '}';
    }
    std::cout << "}}\n";
}

void json_column(const std::string &table, const ColumnRef &column) {
    std::cout << "{\"table\":";
    json_string(table);
    std::cout << ",\"alias\":";
    json_string(column.alias);
    std::cout << ",\"column\":";
    json_string(column.column);
    std::cout << '}';
}

void trace_response(const TranslationTrace &trace) {
    std::cout << "{\"joins\":[";
    for (std::size_t i = 0; i < trace.joins.size(); ++i) {
        if (i != 0) std::cout << ',';
        const auto &entry = trace.joins[i];
        std::cout << "{\"role\":";
        json_string(entry.role);
        std::cout << ",\"source\":";
        json_column(entry.source_table, entry.join.source);
        std::cout << ",\"target\":";
        json_column(entry.join.target_table, entry.join.target);
        std::cout << '}';
    }
    std::cout << "],\"paths\":[";
    for (std::size_t i = 0; i < trace.paths.size(); ++i) {
        if (i != 0) std::cout << ',';
        const auto &entry = trace.paths[i];
        std::cout << "{\"clause\":";
        json_string(entry.clause == PathClause::Select ? "select" : "where");
        std::cout << ",\"index\":" << entry.index << ",\"root\":";
        json_string(entry.path.root());
        std::cout << ",\"features\":[";
        const auto &features = entry.path.features();
        for (std::size_t j = 0; j < features.size(); ++j) {
            if (j != 0) std::cout << ',';
            json_string(features[j]);
        }
        std::cout << "],\"join_ids\":[";
        for (std::size_t j = 0; j < entry.join_ids.size(); ++j) {
            if (j != 0) std::cout << ',';
            std::cout << entry.join_ids[j];
        }
        std::cout << "],\"column\":";
        json_column(entry.terminal_table, entry.column);
        std::cout << '}';
    }
    std::cout << "]}";
}

void success_response(const CompiledQuery &compiled) {
    std::cout << "{\"version\":1,\"ok\":true,\"sql\":";
    json_string(compiled.sql);
    std::cout << ",\"parameters\":[";
    for (std::size_t i = 0; i < compiled.parameters.size(); ++i) {
        if (i != 0) std::cout << ',';
        const auto &value = compiled.parameters[i];
        const bool integer = std::holds_alternative<std::int64_t>(value);
        std::cout << "{\"type\":";
        json_string(integer ? "int64" : "string");
        std::cout << ",\"value\":";
        json_string(integer ? std::to_string(std::get<std::int64_t>(value)) : std::get<std::string>(value));
        std::cout << '}';
    }
    std::cout << "],\"trace\":";
    trace_response(compiled.trace);
    std::cout << "}\n";
}

std::string folded(std::string name) {
    for (auto &character : name) {
        if (character >= 'A' && character <= 'Z') character += 'a' - 'A';
    }
    return name;
}

Catalog read_arguments(int argc, char **argv) {
    Catalog catalog;
    for (int i = 1; i < argc;) {
        const std::string_view option{argv[i++]};
        const int fields = option == "--column" ? 4 : option == "--reference" ? 5 : 0;
        if (fields == 0) throw std::invalid_argument{"Unknown option; use --help for usage"};
        if (argc - i < fields) throw std::invalid_argument{"Missing fields for " + std::string{option}};
        if (option == "--column") {
            const std::string table{argv[i]}, column{argv[i + 1]};
            const std::string_view type{argv[i + 2]}, uniqueness{argv[i + 3]};
            if (type != "integer" && type != "string") {
                throw std::invalid_argument{"Column type must be integer or string"};
            }
            if (uniqueness != "unique" && uniqueness != "nonunique") {
                throw std::invalid_argument{"Column uniqueness must be unique or nonunique"};
            }
            auto table_it = catalog.tables.begin();
            while (table_it != catalog.tables.end() && table_it->name != table) ++table_it;
            if (table_it == catalog.tables.end()) {
                catalog.tables.push_back({table, {}});
                table_it = std::prev(catalog.tables.end());
            }
            table_it->columns.push_back({column, type == "integer" ? ColumnType::Integer : ColumnType::String,
                                         uniqueness == "unique"});
        } else {
            catalog.references.push_back({argv[i], argv[i + 1], argv[i + 2], argv[i + 3], argv[i + 4]});
        }
        i += fields;
    }
    return catalog;
}

void validate_catalog(const Catalog &catalog) {
    if (catalog.tables.empty()) throw std::invalid_argument{"Supply at least one --column"};
    std::set<std::string> tables;
    for (const auto &table : catalog.tables) {
        if (table.name.empty() || !tables.insert(folded(table.name)).second) {
            throw std::invalid_argument{"Empty or duplicate table name: " + table.name};
        }
        std::set<std::string> columns;
        for (const auto &column : table.columns) {
            if (column.name.empty() || !columns.insert(folded(column.name)).second) {
                throw std::invalid_argument{"Empty or duplicate column in table: " + table.name};
            }
        }
    }
    std::set<std::pair<std::string, std::string>> roles;
    for (const auto &reference : catalog.references) {
        if (reference.feature.empty() || !roles.insert({reference.source_table, folded(reference.feature)}).second) {
            throw std::invalid_argument{"Empty or duplicate reference role: " + reference.feature};
        }
        const auto &source = find_column(catalog, reference.source_table, reference.source_column);
        const auto &target = find_column(catalog, reference.target_table, reference.target_column);
        if (source.type != target.type) throw std::invalid_argument{"Reference key types do not match: " + reference.feature};
        if (!target.unique) throw std::invalid_argument{"Reference target must be declared unique: " + reference.feature};
    }
}

} // namespace

int main(int argc, char **argv) {
    if (argc == 2 && std::string_view{argv[1]} == "--help") {
        std::cout << help;
        return finish(0);
    }
    std::string stage{"arguments"};
    try {
        for (int i = 1; i < argc; ++i) {
            if (!valid_utf8(argv[i])) throw std::invalid_argument{"Arguments must be valid UTF-8"};
        }
        const auto catalog = read_arguments(argc, argv);
        stage = "catalog";
        validate_catalog(catalog);
        stage = "input";
        std::string source;
        for (char character; std::cin.get(character);) source += character;
        // With synchronized streams, stdio can expose a read error as EOF.
        if (std::cin.bad() || !std::cin.eof() || std::ferror(stdin)) {
            throw std::invalid_argument{"Could not read SQLP from stdin"};
        }
        if (!valid_utf8(source)) throw std::invalid_argument{"SQLP input must be valid UTF-8"};
        stage = "compiler";
        success_response(compile_sqlp(source, catalog));
        return finish(0);
    } catch (const LexerError &error) {
        error_response("lexer", error.what(), error.location());
    } catch (const ParseError &error) {
        error_response("parser", error.what(), error.location());
    } catch (const std::invalid_argument &error) {
        error_response(stage, error.what());
        return finish(stage == "arguments" ? 2 : 1);
    } catch (const std::exception &) {
        error_response("internal", "Unexpected compiler failure");
        return finish(3);
    }
    return finish(1);
}
