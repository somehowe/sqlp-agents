# SQL query guide

Answer the supplied question with one SQLite SELECT statement. Return only the
query, ending with a semicolon; do not return Markdown or an explanation. Use
only the supplied schema and approved relationship facts.

## Supported task profile

- Start from one logical root table with a required alias: `FROM TABLE AS r`.
- Select the requested columns. A projection alias uses `AS name`.
- Follow approved relationships with explicit `INNER JOIN` clauses and their
  supplied source-column = target-column mappings. Alias every joined table.
- Each approved reference has a non-null source key and exactly one target,
  whose key is unique. A finite chain can revisit a table. Use a separate alias
  for each different reference prefix; reuse an alias for the same prefix.
- Filter with column = string/integer literal predicates joined by `AND`.
- Stay within 63 relationship joins, 2,000 projections, and 512 equality
  predicates. These are the fixed prototype profile limits.
- Preserve duplicate result rows unless the question requests distinct results;
  use `SELECT DISTINCT` when requested. No result order is required.
- These tasks do not require aggregates, GROUP BY, OR, NOT, ORDER BY, LIMIT,
  subqueries, set operators, or navigation through missing/optional references.
  Do not add them. Use literal values rather than parameter placeholders.

Use exact schema names. Bare identifiers contain ASCII letters, digits, and
underscores, starting with a letter or underscore. Double-quote other identifiers
and reserved words; double an embedded double quote, as in `"a""b"`. Strings use
single quotes and doubled embedded single quotes, as in `'O''Neil'`; backslashes
are literal. Integers range from -9223372036854775808 to 9223372036854775807;
write a negative sign adjacent to the digits. Do not use real or NULL literals.

## Example

This example is separate from the supplied task. Schema:

- `EMPLOYEE(eid integer unique non-null, name string, department_id integer non-null)`
- `DEPARTMENT(did integer unique non-null, name string, region string)`
- Approved reference `EMPLOYEE.department` uses
  `EMPLOYEE.department_id = DEPARTMENT.did`, with exactly one target per employee.

Question: List the employee name and department name for employee identifier 7
when that employee's department is in region West.

```sql
SELECT e.name, d.name
FROM EMPLOYEE AS e
INNER JOIN DEPARTMENT AS d ON e.department_id = d.did
WHERE d.region = 'West' AND e.eid = 7;
```

## Existence and distinct names

Using the same unrelated example schema, list department names with at least
one employee named Ada. Return each name once.

```sql
SELECT DISTINCT d.name
FROM EMPLOYEE AS e
INNER JOIN DEPARTMENT AS d ON e.department_id = d.did
WHERE e.name = 'Ada';
```
