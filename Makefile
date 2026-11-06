CXX := g++
BISON := bison
CPPFLAGS := -Isrc -Ibuild
CXXFLAGS := -std=c++20 -Wall -Wextra -Wpedantic -g
SANITIZER_FLAGS := -O1 -D_GLIBCXX_ASSERTIONS -fsanitize=address,undefined -fno-omit-frame-pointer

.PHONY: all run test test-viewer test-sanitize clean

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
	python3 tests/compiler_catalog_test.py
	python3 tests/dataset_check_test.py
	python3 tests/schema_inspection_test.py
	python3 tests/arm_mapping_test.py
	python3 tests/chinook_test.py
	python3 tests/path_study_test.py
	python3 tests/join_views_test.py
	python3 tests/comparison_test.py
	python3 tests/model_provider_test.py
	python3 tests/credentials_test.py
	python3 tests/research_budget_test.py
	python3 tests/pilot_test.py
	python3 tests/batch_pilot_test.py
	python3 tests/natsql_pilot_test.py
	python3 tests/analysis_test.py
	python3 tests/snapshot_evaluation_test.py
	python3 tests/demo_test.py
	python3 tests/demo_server_test.py
	python3 tests/demo_bundle_test.py
	python3 tests/offline_example_test.py
	python3 tests/experiment_kit_test.py
	python3 tests/review_packet_test.py
	python3 tests/benchmark_test.py
	python3 tests/natsql_test.py
	python3 tests/differential_test.py

test-viewer:
	node tests/viewer_test.js
	node tests/lab_test.js

build/compiler_fuzz_sanitize: tests/compiler_fuzz.cc src/parser.cc src/parser.h \
				build/sqlp_parser.cc build/sqlp_parser.h src/lexer.cc src/lexer.h \
				src/query.cc src/query.h src/sql.cc src/sql.h src/catalog.cc \
				src/catalog.h src/mapping.cc src/mapping.h src/path.h Makefile
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(SANITIZER_FLAGS) tests/compiler_fuzz.cc \
	src/parser.cc build/sqlp_parser.cc src/lexer.cc src/query.cc src/sql.cc \
	src/catalog.cc src/mapping.cc -o $@

build/lexer_test_sanitize: tests/lexer_test.cc src/lexer.cc src/lexer.h Makefile
	mkdir -p build
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(SANITIZER_FLAGS) tests/lexer_test.cc src/lexer.cc -o $@

test-sanitize: build/compiler_fuzz_sanitize build/lexer_test_sanitize
	./build/lexer_test_sanitize
	./build/compiler_fuzz_sanitize

clean:
	rm -rf build
