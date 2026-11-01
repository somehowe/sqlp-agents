# Ryan's schedule — C++ compiler

Build the C++ SQLP-to-SQL compiler. It returns SQL, parameters, path mappings, or useful errors; SQLite executes the SQL. The current code has one-hop helpers, not a complete compiler.

**Current course proposal:** draft the opening/problem and SQLP motivation by Canvas's actual deadline, separately from the project dates below.

| Due | Deliverable |
|---|---|
| **Wed Oct 28** | Agree on catalog/compiler formats. Trace Test 10 and sketch the query AST. |
| **Sat Oct 31** | Implement `compile_query` from query objects, returning SQL and separate parameters. Check invalid names, filters, and reused joins. Execute one example with Iris and check the dataset's reference assumptions. |
| **Wed Nov 4** | Hand Iris a minimal tokenizer/parser and callable CLI: one real SQLP text query plus catalog produces SQL/parameters or an error. Bison should build the same AST. |
| **Sat Nov 7** | Finish multi-hop paths, generated aliases, shared joins, and basic checks. Supply Sam a path-to-join record. Freeze and document the evaluated language subset. |
| **Mon Nov 9** | Supply architecture, supported features, and a query trace for the proposed internal outline review with Enamul. |
| **Wed Nov 11** | Help verify references and contrasting data cases before the held-out workload freezes. |
| **Sat Nov 14** | Support evaluation with correctness fixes only. Coordinate reruns after fixes; preserve the frozen feature profile. |
| **Wed Nov 18** | Finish architecture text and assemble the four-page draft from everyone's sections, figures, and results. Review technical claims. |
| **Sat Nov 21** | Verify a clean build, regression checks, instructions, and demo queries. Rehearse tracing one query through the compiler. |
| **Sun Nov 22** | Review/approve the PDF and artifact, confirm authorship with the group, and coordinate submission. Reserve Nov 23 for submission issues. |

**Required subset:** projections, one FROM binding/alias, multi-hop paths, literal equality/AND, DISTINCT, parameters, and basic checks. EXISTS, UNION, and broader syntax remain later goals. If the Nov 4 handoff slips, simplify the first parser immediately and flag the dependency.

**Learning:** work in small increments you can explain. Use AI for bounded help and counterexamples. A large unfamiliar generated implementation would undermine the goal.

[Shared schedule](SCHEDULE.md) · [Agent context](../AGENTS.md)
