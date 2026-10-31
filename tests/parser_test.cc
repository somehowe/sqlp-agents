#include "parser.h"

#include <iostream>
#include <limits>
#include <vector>

namespace {

void require(bool condition, const std::string &message) {
    if (!condition) {
        throw std::runtime_error{message};
    }
}

void require_parse_error(const std::string &input, SourceLocation location) {
    try {
        parse_query(input);
    } catch (const ParseError &error) {
        require(error.location() == location, "Parser reported the wrong source location");
        require(std::string{error.what()}.find("syntax error") != std::string::npos,
                "Parser omitted the syntax diagnostic");
        return;
    }
    throw std::runtime_error{"Accepted invalid query: " + input};
}

void require_lexer_error(const std::string &input, SourceLocation location) {
    try {
        parse_query(input);
    } catch (const LexerError &error) {
        require(error.location() == location, "Parser lost the lexer error location");
        return;
    }
    throw std::runtime_error{"Parser accepted a lexical error"};
}

void require_compile_error(const std::string &input, const Catalog &catalog) {
    // Confirm the input is syntactically valid before testing semantic rejection.
    parse_query(input);
    try {
        compile_sqlp(input, catalog);
    } catch (const std::invalid_argument &) {
        return;
    }
    throw std::runtime_error{"Compiler accepted a semantic error"};
}

} // namespace

int main() {
    const Catalog catalog{
        {
            {"AUTHOR", {{"aid", ColumnType::Integer, true}, {"name", ColumnType::String},
                        {"uni_id", ColumnType::Integer}}},
            {"PAPER", {{"pid", ColumnType::Integer, true}, {"title", ColumnType::String}}},
            {"WROTE", {{"author_aid", ColumnType::Integer}, {"paper_pid", ColumnType::Integer}}},
            {"UNIVERSITY", {{"uid", ColumnType::Integer, true}, {"country", ColumnType::String}}}
        },
        {
            {"WROTE", "author", "author_aid", "AUTHOR", "aid"},
            {"WROTE", "paper", "paper_pid", "PAPER", "pid"},
            {"AUTHOR", "university", "uni_id", "UNIVERSITY", "uid"}
        }
    };
    const std::string example =
        "SELECT DISTINCT w.author.name AS author_name FROM WROTE AS w "
        "WHERE w.paper.title = 'Databases';";

    try {
        const auto parsed = parse_query(example);
        require(parsed.source_table == "WROTE" && parsed.source_alias == "w" && parsed.distinct,
                "FROM binding or DISTINCT was lost");
        require(parsed.projections.size() == 1 &&
                    parsed.projections[0].path.root() == "w" &&
                    parsed.projections[0].path.features() == std::vector<std::string>{"author", "name"} &&
                    parsed.projections[0].alias == "author_name", "Projection AST was incorrect");
        require(parsed.where.size() == 1 && parsed.where[0].path.root() == "w" &&
                    parsed.where[0].path.features() == std::vector<std::string>{"paper", "title"} &&
                    parsed.where[0].value == SqlValue{std::string{"Databases"}}, "Predicate AST was incorrect");

        const Query manual{
            "WROTE", "w", {{Path{"w", {"author", "name"}}, "author_name"}},
            {EqualityPredicate{Path{"w", {"paper", "title"}}, std::string{"Databases"}}}, true
        };
        const auto from_text = compile_sqlp(example, catalog);
        const auto from_objects = compile_query(manual, catalog);
        require(from_text.sql == from_objects.sql && from_text.parameters == from_objects.parameters,
                "Text and object compilation disagreed");

        const auto simple = parse_query("select a.name, a.aid AS id from AUTHOR a; -- finished\n");
        require(!simple.distinct && simple.where.empty() && simple.projections.size() == 2 &&
                    !simple.projections[0].alias && simple.projections[1].alias == "id",
                "Optional fields or multiple projections were incorrect");
        const auto escaped = parse_query("SELECT a.name FROM AUTHOR a WHERE a.name = 'O''Brien';");
        require(escaped.where[0].value == SqlValue{std::string{"O'Brien"}}, "String escaping was lost");

        const std::string and_query =
            "SELECT a.name FROM AUTHOR a WHERE a.aid = 1 and a.name = 'AND' AND a.aid = 2;";
        const auto conjunction = parse_query(and_query);
        require(conjunction.where.size() == 3 &&
                    conjunction.where[0].path.features() == std::vector<std::string>{"aid"} &&
                    conjunction.where[0].value == SqlValue{std::int64_t{1}} &&
                    conjunction.where[1].path.features() == std::vector<std::string>{"name"} &&
                    conjunction.where[1].value == SqlValue{std::string{"AND"}} &&
                    conjunction.where[2].value == SqlValue{std::int64_t{2}},
                "Conjunction AST lost condition order, literal contents, or value types");
        require(compile_sqlp(and_query, catalog).parameters ==
                    std::vector<SqlValue>{std::int64_t{1}, std::string{"AND"}, std::int64_t{2}},
                "Text-to-SQL conjunction lost parameter order");

        const auto keyword_name = parse_query(
            "SELECT a.\"AND\" FROM AUTHOR a WHERE a.\"AND\" = 'AND' AND a.aid = 1;");
        require(keyword_name.where.size() == 2 &&
                    keyword_name.where[0].path.features() == std::vector<std::string>{"AND"},
                "A quoted keyword was treated as a conjunction");

        const auto minimum = compile_sqlp(
            "SELECT a.name FROM AUTHOR a WHERE a.aid = -9223372036854775808;", catalog);
        const auto maximum = compile_sqlp(
            "SELECT a.name FROM AUTHOR a WHERE a.aid = 9223372036854775807;", catalog);
        require(minimum.parameters == std::vector<SqlValue>{std::numeric_limits<std::int64_t>::min()} &&
                    maximum.parameters == std::vector<SqlValue>{std::numeric_limits<std::int64_t>::max()},
                "Parser lost int64 literal values");

        const Catalog reserved{{{"order", {{"select", ColumnType::String}}}}, {}};
        const auto quoted = compile_sqlp(
            "SELECT \"from\".\"select\" AS \"display\"\"name\" FROM \"order\" AS \"from\";", reserved);
        require(quoted.sql ==
                    "SELECT \"from\".\"select\" AS \"display\"\"name\"\nFROM \"order\" AS \"from\";",
                "Quoted identifiers were not preserved through parsing");

        const auto long_path = parse_query("SELECT w.author.university.country FROM WROTE w;");
        require(long_path.projections[0].path.features().size() == 3,
                "Parser lost a longer path component");
        const auto long_sql = compile_sqlp("SELECT w.author.university.country FROM WROTE w;", catalog);
        require(long_sql.sql.find("SELECT \"w_ref1\".\"country\"") == 0 &&
                    long_sql.sql.find("ON \"w_ref0\".\"uni_id\" = \"w_ref1\".\"uid\"") != std::string::npos,
                "Text-to-SQL compilation lost the second reference hop");
        require_compile_error("SELECT w.author.missing.country FROM WROTE w;", catalog);
        require_compile_error("SELECT a.missing FROM AUTHOR a;", catalog);
        require_compile_error("SELECT w.name FROM AUTHOR a;", catalog);
        require_compile_error("SELECT a.name FROM AUTHOR a WHERE a.aid = 'wrong type';", catalog);

        require_parse_error("", {0, 1, 1});
        const std::string missing_semicolon{"SELECT a.name FROM AUTHOR a"};
        require_parse_error(missing_semicolon,
                            {missing_semicolon.size(), 1, missing_semicolon.size() + 1});
        for (const auto &invalid : std::vector<std::string>{
                 "SELECT a.name AUTHOR a;", "SELECT a. FROM AUTHOR a;",
                 "SELECT a.name, FROM AUTHOR a;", "SELECT a.name label FROM AUTHOR a;",
                 "SELECT a.name FROM AUTHOR;", "SELECT FROM AUTHOR a;",
                 "SELECT a.name FROM AUTHOR a;;", "FROM AUTHOR a;",
                 "SELECT a.name FROM AUTHOR a WHERE;",
                 "SELECT a.name FROM AUTHOR a WHERE AND a.aid = 1;",
                 "SELECT a.name FROM AUTHOR a WHERE a.aid = 1 AND AND a.name = 'Alice';",
                 "SELECT a.name FROM AUTHOR a WHERE a.aid = 1 AND a.name =;"}) {
            bool rejected = false;
            try { parse_query(invalid); }
            catch (const ParseError &) { rejected = true; }
            require(rejected, "Malformed query was accepted: " + invalid);
        }
        const std::string second_statement = "SELECT a.name FROM AUTHOR a; SELECT a.aid FROM AUTHOR a;";
        const auto second_start = second_statement.find("SELECT", 1);
        require_parse_error(second_statement, {second_start, 1, second_start + 1});
        const std::string extra = "SELECT a.name FROM AUTHOR a; extra";
        const auto extra_start = extra.find("extra");
        require_parse_error(extra, {extra_start, 1, extra_start + 1});
        const std::string trailing_and = "SELECT a.name FROM AUTHOR a WHERE a.aid = 1 AND ;";
        require_parse_error(trailing_and, {trailing_and.find(';'), 1, trailing_and.find(';') + 1});
        const std::string or_query = "SELECT a.name FROM AUTHOR a WHERE a.aid = 1 OR a.name = 'Alice';";
        const auto or_start = or_query.find(" OR ") + 1;
        require_parse_error(or_query, {or_start, 1, or_start + 1});
        const std::string multiline = "SELECT a.name\r\nFROM AUTHOR a\r\nWHERE a.aid = ;";
        require_parse_error(multiline, {multiline.find(';'), 3, 15});

        const std::string bad_comment = "SELECT a.name FROM AUTHOR a; /* unfinished";
        const auto comment_start = bad_comment.find("/*");
        require_lexer_error(bad_comment, {comment_start, 1, comment_start + 1});
        const std::string bad_number = "SELECT a.name FROM AUTHOR a WHERE a.aid = 9223372036854775808;";
        const auto number_start = bad_number.find("922");
        require_lexer_error(bad_number, {number_start, 1, number_start + 1});
        require(parse_query(example).distinct, "A failed parse contaminated a later valid parse");

        std::cout << "parser checks passed\n";
    } catch (const std::exception &error) {
        std::cerr << "parser check failed: " << error.what() << '\n';
        return 1;
    }
}
