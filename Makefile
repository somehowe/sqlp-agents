CXX := g++
BISON := bison
CPPFLAGS := -Isrc -Ibuild
CXXFLAGS := -std=c++20 -Wall -Wextra -Wpedantic -g

.PHONY: all run test clean

all: build/sqlp_demo build/sqlpc

build/sqlp_demo: src/main.cc src/path.h Makefile
	mkdir -p build
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) src/main.cc -o $@

build/path_test: tests/path_test.cc src/path.h Makefile
	mkdir -p build
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) tests/path_test.cc -o $@

build/mapping_test: tests/mapping_test.cc src/mapping.cc src/mapping.h Makefile
	mkdir -p build
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) tests/mapping_test.cc src/mapping.cc -o $@

build/sql_test: tests/sql_test.cc src/sql.cc src/sql.h src/mapping.cc \
				src/mapping.h src/path.h src/catalog.cc src/catalog.h Makefile
	mkdir -p build
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) tests/sql_test.cc \
	src/sql.cc src/mapping.cc src/catalog.cc -o $@

build/query_test: tests/query_test.cc src/query.cc src/query.h src/sql.cc \
				src/sql.h src/catalog.cc src/catalog.h src/mapping.cc \
				src/mapping.h src/path.h Makefile
	mkdir -p build
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) tests/query_test.cc src/query.cc \
	src/sql.cc src/catalog.cc src/mapping.cc -o $@

build/lexer_test: tests/lexer_test.cc src/lexer.cc src/lexer.h Makefile
	mkdir -p build
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) tests/lexer_test.cc src/lexer.cc -o $@

# Grouped outputs prevent parallel builds from running Bison twice.
build/sqlp_parser.cc build/sqlp_parser.h &: src/parser.yy src/parser.h \
				src/query.h src/lexer.h Makefile
	mkdir -p build
	$(BISON) --warnings=all,error --defines=build/sqlp_parser.h \
	--output=build/sqlp_parser.cc src/parser.yy

build/parser_test: tests/parser_test.cc src/parser.cc src/parser.h \
				build/sqlp_parser.cc build/sqlp_parser.h src/lexer.cc src/lexer.h \
				src/query.cc src/query.h src/sql.cc src/sql.h src/catalog.cc \
				src/catalog.h src/mapping.cc src/mapping.h src/path.h Makefile
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) tests/parser_test.cc src/parser.cc \
	build/sqlp_parser.cc src/lexer.cc src/query.cc src/sql.cc \
	src/catalog.cc src/mapping.cc -o $@

build/sqlpc: src/compiler_main.cc src/parser.cc src/parser.h \
				build/sqlp_parser.cc build/sqlp_parser.h src/lexer.cc src/lexer.h \
				src/query.cc src/query.h src/sql.cc src/sql.h src/catalog.cc \
				src/catalog.h src/mapping.cc src/mapping.h src/path.h Makefile
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) src/compiler_main.cc src/parser.cc \
	build/sqlp_parser.cc src/lexer.cc src/query.cc src/sql.cc \
	src/catalog.cc src/mapping.cc -o $@

run: build/sqlp_demo
	@./build/sqlp_demo

test: build/path_test build/mapping_test build/sql_test build/query_test build/lexer_test build/parser_test build/sqlpc
	@./build/path_test
	@./build/mapping_test
	@./build/sql_test
	@./build/query_test
	@./build/lexer_test
	@./build/parser_test
	python3 tests/compiler_cli_test.py

clean:
	rm -rf build
