#ifndef SQLP_QUERY_H
#define SQLP_QUERY_H

#include "catalog.h"
#include "path.h"

#include <cstdint>
#include <optional>
#include <string>
#include <variant>
#include <vector>

using SqlValue = std::variant<std::int64_t, std::string>;

struct Projection {
    Path path;
    std::optional<std::string> alias;
};

struct EqualityPredicate {
    Path path;
    SqlValue value;
};

// One FROM binding and a conjunction of literal equalities.
// Both the text parser and C++ callers construct these objects.
struct Query {
    std::string source_table;
    std::string source_alias;
    std::vector<Projection> projections;
    std::vector<EqualityPredicate> where; // Empty means no WHERE clause.
    bool distinct = false;
};

struct CompiledQuery {
    std::string sql;
    std::vector<SqlValue> parameters;
};

// Supports direct columns and finite multi-hop reference paths. Literal values
// are bound separately; invalid names, reference metadata, and type errors throw
// std::invalid_argument. Catalog declarations do not validate actual rows.
CompiledQuery compile_query(const Query &query, const Catalog &catalog);

#endif
