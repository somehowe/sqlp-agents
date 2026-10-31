#include "query.h"

#include <iostream>
#include <limits>
#include <stdexcept>

namespace {

void require(bool condition, const std::string &message) {
    if (!condition) {
        throw std::runtime_error{message};
    }
}

void require_invalid(const Query &query, const Catalog &catalog, const std::string &message) {
    try {
        compile_query(query, catalog);
    } catch (const std::invalid_argument &) {
        return;
    }
    throw std::runtime_error{message};
}

std::size_t join_count(const std::string &sql) {
    std::size_t count = 0;
    for (std::size_t position = 0; (position = sql.find("\nJOIN ", position)) != std::string::npos;
         position += 6) {
        ++count;
    }
    return count;
}

} // namespace

int main() {
    const Catalog catalog{
        {
            {"AUTHOR", {{"aid", ColumnType::Integer, true}, {"name", ColumnType::String},
                        {"uni_id", ColumnType::Integer}, {"mentor_aid", ColumnType::Integer}}},
            {"PAPER", {{"pid", ColumnType::Integer, true}, {"title", ColumnType::String},
                       {"lead_aid", ColumnType::Integer}, {"editor_aid", ColumnType::Integer}}},
            {"WROTE", {{"author_aid", ColumnType::Integer}, {"paper_pid", ColumnType::Integer}}},
            {"UNIVERSITY", {{"uid", ColumnType::Integer, true}, {"country", ColumnType::String}}}
        },
        {
            {"WROTE", "author", "author_aid", "AUTHOR", "aid"},
            {"WROTE", "paper", "paper_pid", "PAPER", "pid"},
            {"PAPER", "lead_author", "lead_aid", "AUTHOR", "aid"},
            {"PAPER", "editor", "editor_aid", "AUTHOR", "aid"},
            {"AUTHOR", "university", "uni_id", "UNIVERSITY", "uid"},
            {"AUTHOR", "mentor", "mentor_aid", "AUTHOR", "aid"}
        }
    };
    const Query example{
        "WROTE", "w", {{Path{"w", {"author", "name"}}, "author_name"}},
        {EqualityPredicate{Path{"w", {"paper", "title"}}, std::string{"Databases"}}}, true
    };

    try {
        const auto compiled = compile_query(example, catalog);
        const std::string expected =
            "SELECT DISTINCT \"w_ref0\".\"name\" AS \"author_name\"\n"
            "FROM \"WROTE\" AS \"w\"\n"
            "JOIN \"AUTHOR\" AS \"w_ref0\" ON \"w\".\"author_aid\" = \"w_ref0\".\"aid\"\n"
            "JOIN \"PAPER\" AS \"w_ref1\" ON \"w\".\"paper_pid\" = \"w_ref1\".\"pid\"\n"
            "WHERE \"w_ref1\".\"title\" = ?;";
        require(compiled.sql == expected, "Complete query SQL did not match");
        require(compiled.parameters == std::vector<SqlValue>{std::string{"Databases"}},
                "String parameter was not returned separately");

        Query direct{
            "AUTHOR", "a", {{Path{"a", {"name"}}, std::nullopt}},
            {EqualityPredicate{Path{"a", {"aid"}}, std::numeric_limits<std::int64_t>::max()}}
        };
        const auto direct_result = compile_query(direct, catalog);
        require(join_count(direct_result.sql) == 0, "Direct columns unexpectedly added a join");
        require(direct_result.sql.find("DISTINCT") == std::string::npos,
                "Compiler added DISTINCT without being asked");
        require(direct_result.parameters ==
                    std::vector<SqlValue>{std::numeric_limits<std::int64_t>::max()},
                "Integer parameter lost its value or type");
        direct.where.clear();
        require(compile_query(direct, catalog).parameters.empty(),
                "Query without a predicate unexpectedly returned parameters");

        Query repeated{
            "WROTE", "w", {{Path{"w", {"author", "name"}}, "name"},
                           {Path{"w", {"author", "aid"}}, "id"}},
            {EqualityPredicate{Path{"w", {"author", "name"}}, std::string{"Alice"}}}
        };
        require(join_count(compile_query(repeated, catalog).sql) == 1,
                "Repeated reference paths did not reuse their join");

        const Query roles{
            "PAPER", "p", {{Path{"p", {"lead_author", "name"}}, "lead"},
                           {Path{"p", {"editor", "name"}}, "editor"}}, {}
        };
        const auto role_result = compile_query(roles, catalog);
        require(join_count(role_result.sql) == 2, "Different reference roles were merged");
        require(role_result.sql.find("\"p_ref0\".\"name\" AS \"lead\"") != std::string::npos &&
                    role_result.sql.find("\"p_ref1\".\"name\" AS \"editor\"") != std::string::npos,
                "Reference roles did not use distinct aliases");

        const Query multi_hop{
            "WROTE", "w", {{Path{"w", {"author", "name"}}, std::nullopt},
                           {Path{"w", {"author", "university", "country"}}, std::nullopt}},
            {EqualityPredicate{Path{"w", {"author", "university", "country"}}, std::string{"CA"}}}
        };
        const auto multi_result = compile_query(multi_hop, catalog);
        require(multi_result.sql ==
                    "SELECT \"w_ref0\".\"name\", \"w_ref1\".\"country\"\n"
                    "FROM \"WROTE\" AS \"w\"\n"
                    "JOIN \"AUTHOR\" AS \"w_ref0\" ON \"w\".\"author_aid\" = \"w_ref0\".\"aid\"\n"
                    "JOIN \"UNIVERSITY\" AS \"w_ref1\" ON \"w_ref0\".\"uni_id\" = \"w_ref1\".\"uid\"\n"
                    "WHERE \"w_ref1\".\"country\" = ?;",
                "Multi-hop paths did not reuse their prefixes or chain the correct join sources");
        require(multi_result.parameters == std::vector<SqlValue>{std::string{"CA"}},
                "Deep predicate parameter lost its value or type");

        Query conjunction = multi_hop;
        conjunction.where = {
            {Path{"w", {"paper", "pid"}}, std::int64_t{10}},
            {Path{"w", {"author", "university", "country"}}, std::string{"CA"}},
            {Path{"w", {"author", "aid"}}, std::int64_t{1}},
            {Path{"w", {"paper", "title"}}, std::string{"Databases"}}
        };
        const auto and_result = compile_query(conjunction, catalog);
        require(and_result.sql.find(
                    "\nWHERE \"w_ref2\".\"pid\" = ? AND \"w_ref1\".\"country\" = ?"
                    " AND \"w_ref0\".\"aid\" = ? AND \"w_ref2\".\"title\" = ?;") != std::string::npos,
                "Conjunction terms did not preserve source order");
        require(and_result.parameters == std::vector<SqlValue>{
                    std::int64_t{10}, std::string{"CA"}, std::int64_t{1}, std::string{"Databases"}},
                "Conjunction parameters lost their order or types");
        require(join_count(and_result.sql) == 3, "Conjunction terms did not reuse shared joins");

        const Query branches{
            "PAPER", "p", {{Path{"p", {"lead_author", "university", "country"}}, "lead_country"},
                           {Path{"p", {"editor", "university", "country"}}, "editor_country"}}, {}
        };
        const auto branch_result = compile_query(branches, catalog);
        require(join_count(branch_result.sql) == 4 &&
                    branch_result.sql.find("ON \"p_ref0\".\"uni_id\" = \"p_ref1\".\"uid\"") != std::string::npos &&
                    branch_result.sql.find("ON \"p_ref2\".\"uni_id\" = \"p_ref3\".\"uid\"") != std::string::npos,
                "The same downstream role was merged across different source bindings");

        const Query revisits{
            "AUTHOR", "a", {{Path{"a", {"mentor", "name"}}, std::nullopt},
                            {Path{"a", {"mentor", "mentor", "name"}}, std::nullopt}},
            {EqualityPredicate{Path{"a", {"mentor", "mentor", "aid"}}, std::int64_t{1}}}
        };
        const auto revisit_result = compile_query(revisits, catalog);
        require(join_count(revisit_result.sql) == 2 &&
                    revisit_result.sql.find("ON \"a_ref0\".\"mentor_aid\" = \"a_ref1\".\"aid\"") != std::string::npos,
                "Finite paths revisiting the same table did not get distinct bindings");

        Catalog dotted_role = catalog;
        dotted_role.tables[2].columns.push_back({"uni_uid", ColumnType::Integer});
        dotted_role.references.push_back({"WROTE", "author.university", "uni_uid", "UNIVERSITY", "uid"});
        Query dotted_paths = multi_hop;
        dotted_paths.projections.push_back({Path{"w", {"author.university", "country"}}, std::nullopt});
        require(join_count(compile_query(dotted_paths, dotted_role).sql) == 3,
                "A quoted role containing a dot collided with a multi-component prefix");

        Query collision{
            "WROTE", "W_REF0", {{Path{"W_REF0", {"author", "name"}}, std::nullopt}}, {}
        };
        require(compile_query(collision, catalog).sql.find("AS \"W_REF0_ref0\"") != std::string::npos,
                "Generated alias collided with the FROM alias");

        Query quoted = example;
        const std::string literal{"O'Reilly' OR 1=1 --"};
        quoted.where[0].value = literal;
        const auto quoted_result = compile_query(quoted, catalog);
        require(quoted_result.sql == compiled.sql &&
                    quoted_result.parameters == std::vector<SqlValue>{literal},
                "Literal text was interpolated into SQL");

        const Catalog reserved{{{"order", {{"select", ColumnType::String}}}}, {}};
        const Query reserved_query{
            "order", "from", {{Path{"from", {"select"}}, "display\"name"}}, {}
        };
        require(compile_query(reserved_query, reserved).sql ==
                    "SELECT \"from\".\"select\" AS \"display\"\"name\"\nFROM \"order\" AS \"from\";",
                "SQL identifiers were not quoted correctly");

        Query invalid = example;
        invalid.projections.clear();
        require_invalid(invalid, catalog, "Empty projections were accepted");
        invalid = example;
        invalid.projections[0].path = Path{"other", {"author", "name"}};
        require_invalid(invalid, catalog, "Unbound projection alias was accepted");
        invalid = example;
        invalid.where[0].path = Path{"other", {"paper", "title"}};
        require_invalid(invalid, catalog, "Unbound predicate alias was accepted");
        invalid = example;
        invalid.source_table = "UNKNOWN";
        require_invalid(invalid, catalog, "Unknown source table was accepted");
        invalid = example;
        invalid.projections[0].path = Path{"w", {"author", "missing"}};
        require_invalid(invalid, catalog, "Unknown column was accepted");
        invalid.projections[0].path = Path{"w", {"missing", "name"}};
        require_invalid(invalid, catalog, "Unknown reference was accepted");
        invalid.projections[0].path = Path{"w", {"paper", "editor", "missing"}};
        require_invalid(invalid, catalog, "Unknown deep terminal column was accepted");
        invalid.projections[0].path = Path{"w", {"paper", "missing", "name"}};
        require_invalid(invalid, catalog, "Unknown intermediate reference was accepted");
        invalid = example;
        invalid.where[0].value = std::int64_t{42};
        require_invalid(invalid, catalog, "Wrong predicate literal type was accepted");
        invalid = example;
        invalid.projections[0].alias = "";
        require_invalid(invalid, catalog, "Empty projection alias was accepted");
        invalid = example;
        invalid.source_alias.clear();
        require_invalid(invalid, catalog, "Empty FROM alias was accepted");
        invalid = example;
        invalid.projections[0].alias = std::string{"bad\0alias", 9};
        require_invalid(invalid, catalog, "NUL in an identifier was accepted");

        Catalog invalid_catalog = catalog;
        invalid_catalog.tables[0].columns[0].unique = false;
        require_invalid(example, invalid_catalog, "A non-unique reference target was accepted");
        invalid_catalog = catalog;
        invalid_catalog.tables[2].columns[0].type = ColumnType::String;
        require_invalid(example, invalid_catalog, "Mismatched reference key types were accepted");

        invalid_catalog = catalog;
        invalid_catalog.tables[3].columns[0].unique = false;
        require_invalid(multi_hop, invalid_catalog, "Non-unique target at a later hop was accepted");
        invalid_catalog = catalog;
        invalid_catalog.tables[0].columns[2].type = ColumnType::String;
        require_invalid(multi_hop, invalid_catalog, "Mismatched key types at a later hop were accepted");
        invalid_catalog = catalog;
        invalid_catalog.references[4].source_column = "missing";
        require_invalid(multi_hop, invalid_catalog, "Missing source key at a later hop was accepted");
        invalid_catalog = catalog;
        invalid_catalog.references[4].target_column = "missing";
        require_invalid(multi_hop, invalid_catalog, "Missing target key at a later hop was accepted");
        invalid = multi_hop;
        invalid.where[0].value = std::int64_t{42};
        require_invalid(invalid, catalog, "Wrong literal type for a deep terminal column was accepted");

        // Every condition is checked, even after a contradictory pair.
        invalid = example;
        invalid.where = {
            {Path{"w", {"author", "aid"}}, std::int64_t{1}},
            {Path{"w", {"author", "aid"}}, std::int64_t{2}},
            {Path{"w", {"paper", "title"}}, std::int64_t{10}}
        };
        require_invalid(invalid, catalog, "Wrong literal type in a later condition was accepted");
        invalid.where.back() = {Path{"w", {"paper", "missing"}}, std::string{"Databases"}};
        require_invalid(invalid, catalog, "Unknown column in a later condition was accepted");
        invalid.where.back() = {Path{"other", {"paper", "title"}}, std::string{"Databases"}};
        require_invalid(invalid, catalog, "Unbound alias in a later condition was accepted");

        std::cout << "query compiler checks passed\n";
    } catch (const std::exception &error) {
        std::cerr << "query compiler check failed: " << error.what() << '\n';
        return 1;
    }
}
