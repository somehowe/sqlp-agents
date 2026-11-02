#ifndef SQLP_QUERY_H
#define SQLP_QUERY_H

#include "catalog.h"
#include "path.h"
#include "sql.h"

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <variant>
#include <vector>

using SqlValue = std::variant<std::int64_t, std::string>;

// Fixed prototype profile, keeping generated queries within SQLite's normal
// table, result-column, and expression-depth limits. Runtime limits may be lower.
namespace sqlp {
inline constexpr std::size_t max_reference_joins = 63;
inline constexpr std::size_t max_projections = 2000;
inline constexpr std::size_t max_predicates = 512;
}

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

// Join IDs are positions in TranslationTrace.joins, local to one compilation.
// Names are raw catalog identifiers; SQL quoting happens only when rendering.
struct JoinTrace {
    std::string role;
    std::string source_table;
    JoinSpec join;
};

enum class PathClause { Select, Where };

struct PathTrace {
    PathClause clause;
    std::size_t index; // Position in the projection or WHERE vector.
    Path path;        // Owned, decoded components; a quoted dot stays in one component.
    std::string terminal_table;
    ColumnRef column;
    std::vector<std::size_t> join_ids;
};

struct TranslationTrace {
    std::vector<JoinTrace> joins;
    std::vector<PathTrace> paths; // Every occurrence, SELECT first, then WHERE.
};

struct CompiledQuery {
    std::string sql;
    std::vector<SqlValue> parameters;
    TranslationTrace trace;
};

// Supports direct columns and finite multi-hop reference paths. Literal values
// are bound separately; invalid names, reference metadata, and type errors throw
// std::invalid_argument. Catalog declarations do not validate actual rows.
// The SQLite profile permits at most 63 distinct generated joins (shared paths
// reuse them), 2000 projections, and 512 equality predicates.
CompiledQuery compile_query(const Query &query, const Catalog &catalog);

#endif
