%skeleton "lalr1.cc"
%require "3.8"
%define api.namespace {sqlp::detail}
%define api.parser.class {BisonParser}
%define api.value.type variant
%define api.token.constructor
%define api.token.raw
%define api.location.type {SourceSpan}
%define parse.error detailed
%define parse.lac full
%locations

%code requires {
#include "parser.h"
#include <optional>
#include <utility>
#include <vector>
}

%param { Lexer &lexer }
%parse-param { std::optional<Query> &result }

%code provides {
namespace sqlp::detail {
    BisonParser::symbol_type yylex(Lexer &lexer);
}
}

%code {
namespace {
    Path make_path(std::vector<std::string> parts) {
        std::string root = std::move(parts.front());
        parts.erase(parts.begin());
        return Path{std::move(root), std::move(parts)};
    }
}
}

%token END 0 "end of input"
%token SELECT "SELECT" DISTINCT "DISTINCT" FROM "FROM" AS "AS" WHERE "WHERE" AND "AND"
%token DOT "." COMMA "," EQUAL "=" SEMICOLON ";"
%token <std::string> IDENTIFIER "identifier" STRING "string literal"
%token <std::int64_t> INTEGER "integer literal"

%nterm <bool> distinct
%nterm <std::vector<Projection>> projections
%nterm <std::vector<std::string>> path
%nterm <std::optional<std::string>> projection_alias
%nterm <std::pair<std::string, std::string>> binding
%nterm <std::vector<EqualityPredicate>> where_clause equalities
%nterm <SqlValue> literal
%start input

%%

input:
    SELECT distinct projections FROM binding where_clause SEMICOLON END {
        result = Query{std::move($5.first), std::move($5.second),
                       std::move($3), std::move($6), $2};
    }
;

distinct:
    %empty { $$ = false; }
  | DISTINCT { $$ = true; }
;

projections:
    path projection_alias {
        $$.push_back(Projection{make_path(std::move($1)), std::move($2)});
    }
  | projections COMMA path projection_alias {
        $$ = std::move($1);
        $$.push_back(Projection{make_path(std::move($3)), std::move($4)});
    }
;

path:
    IDENTIFIER DOT IDENTIFIER { $$ = {std::move($1), std::move($3)}; }
  | path DOT IDENTIFIER { $$ = std::move($1); $$.push_back(std::move($3)); }
;

projection_alias:
    %empty { $$ = std::nullopt; }
  | AS IDENTIFIER { $$ = std::move($2); }
;

binding:
    IDENTIFIER IDENTIFIER { $$ = {std::move($1), std::move($2)}; }
  | IDENTIFIER AS IDENTIFIER { $$ = {std::move($1), std::move($3)}; }
;

where_clause:
    %empty { $$ = {}; }
  | WHERE equalities { $$ = std::move($2); }
;

equalities:
    path EQUAL literal {
        $$.push_back(EqualityPredicate{make_path(std::move($1)), std::move($3)});
    }
  | equalities AND path EQUAL literal {
        $$ = std::move($1);
        $$.push_back(EqualityPredicate{make_path(std::move($3)), std::move($5)});
    }
;

literal:
    STRING { $$ = std::move($1); }
  | INTEGER { $$ = $1; }
;
