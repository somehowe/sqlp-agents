#include "query.h"

#include "sql.h"

#include <stdexcept>
#include <utility>

namespace {

std::string quote_identifier(const std::string &name) {
    if (name.empty() || name.find('\0') != std::string::npos) {
        throw std::invalid_argument{"SQL identifiers must be nonempty and contain no NUL"};
    }

    std::string quoted{"\""};
    for (char character : name) {
        if (character == '"') {
            quoted += '"';
        }
        quoted += character;
    }
    return quoted + '"';
}

// Quote identifiers at the rendering boundary, then reuse the SQL renderers.
ColumnRef quoted_column(const ColumnRef &column) {
    return {quote_identifier(column.alias), quote_identifier(column.column)};
}

struct BoundColumn {
    ColumnRef column;
    ColumnType type;
};

bool matches_type(const SqlValue &value, ColumnType type) {
    return (type == ColumnType::Integer && std::holds_alternative<std::int64_t>(value)) ||
           (type == ColumnType::String && std::holds_alternative<std::string>(value));
}

} // namespace

CompiledQuery compile_query(const Query &query, const Catalog &catalog) {
    if (query.projections.empty()) {
        throw std::invalid_argument{"A query needs at least one projection"};
    }

    const std::string source_table = quote_identifier(query.source_table);
    const std::string source_alias = quote_identifier(query.source_alias);
    CompiledQuery result;
    auto &joins = result.trace.joins;

    auto bind_path = [&](const Path &path, PathClause clause, std::size_t occurrence) -> BoundColumn {
        if (path.root() != query.source_alias) {
            throw std::invalid_argument{"Path alias does not match the FROM binding: " + path.root()};
        }

        const auto &features = path.features();
        if (features.empty()) {
            throw std::invalid_argument{"A path needs a terminal column"};
        }

        std::string current_table = query.source_table;
        std::string current_alias = query.source_alias;
        std::vector<std::size_t> join_ids;
        // All components except the last name references; the last names a column.
        for (std::size_t index = 0; index + 1 < features.size(); ++index) {
            const auto &feature = features[index];
            const auto &mapping = find_reference(catalog.references, current_table, feature);
            const auto &source_key = find_column(catalog, current_table, mapping.source_column);
            const auto &target_key = find_column(catalog, mapping.target_table, mapping.target_column);
            if (source_key.type != target_key.type) {
                throw std::invalid_argument{"Reference key types do not match: " + feature};
            }
            if (!target_key.unique) {
                throw std::invalid_argument{"Reference target must be declared unique: " + feature};
            }

            std::string target_alias;
            std::size_t join_id = 0;
            for (; join_id < joins.size(); ++join_id) {
                const auto &reference = joins[join_id];
                // The source binding distinguishes roles reached through different prefixes.
                if (reference.join.source.alias == current_alias && reference.role == feature) {
                    target_alias = reference.join.target.alias;
                    break;
                }
            }
            if (join_id == joins.size()) {
                // A suffix on the root cannot collide with it, even ignoring ASCII case.
                target_alias = query.source_alias + "_ref" + std::to_string(joins.size());
                joins.push_back({feature, current_table, {mapping.target_table,
                                                         {current_alias, mapping.source_column},
                                                         {target_alias, mapping.target_column}}});
            }
            join_ids.push_back(join_id);
            current_table = mapping.target_table;
            current_alias = std::move(target_alias);
        }
        const auto &column = find_column(catalog, current_table, features.back());
        ColumnRef column_ref{std::move(current_alias), features.back()};
        result.trace.paths.push_back({clause, occurrence, path, current_table, column_ref, std::move(join_ids)});
        return {std::move(column_ref), column.type};
    };

    std::string select_sql = query.distinct ? "SELECT DISTINCT " : "SELECT ";
    for (std::size_t index = 0; index < query.projections.size(); ++index) {
        const auto &projection = query.projections[index];
        const auto bound = bind_path(projection.path, PathClause::Select, index);
        if (index != 0) {
            select_sql += ", ";
        }
        select_sql += render_column(quoted_column(bound.column));
        if (projection.alias) {
            select_sql += " AS " + quote_identifier(*projection.alias);
        }
    }

    std::string where_sql;
    for (std::size_t index = 0; index < query.where.size(); ++index) {
        const auto &predicate = query.where[index];
        const auto bound = bind_path(predicate.path, PathClause::Where, index);
        if (!matches_type(predicate.value, bound.type)) {
            throw std::invalid_argument{"Predicate literal type does not match the column type"};
        }
        where_sql += where_sql.empty() ? "\nWHERE " : " AND ";
        where_sql += render_column(quoted_column(bound.column)) + " = ?";
        result.parameters.push_back(predicate.value);
    }

    result.sql = select_sql + "\nFROM " + source_table + " AS " + source_alias;
    for (const auto &reference : joins) {
        const auto &join = reference.join;
        result.sql += "\n" + render_join(JoinSpec{
            quote_identifier(join.target_table),
            quoted_column(join.source),
            quoted_column(join.target)
        });
    }
    result.sql += where_sql + ";";
    return result;
}
