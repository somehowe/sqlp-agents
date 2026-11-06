# NatSQL query guide

Answer the supplied question with one NatSQL SELECT statement. Return only the
query, ending with a semicolon; do not return Markdown or an explanation. Use
only the supplied schema and approved relationship facts.

## Supported task profile

- Write column references as `TABLE.column`, using the physical table names.
  These are not SQLP reference paths. Do not invent column or table aliases.
- Start from the table whose records the question asks about: `FROM TABLE`.
  The pinned NatSQL converter reconstructs SQL joins using the supplied keys.
  Do not write explicit JOIN clauses. If a relationship must be made explicit,
  write the supplied `SOURCE.column = TARGET.column` equality in WHERE.
- Preserve root-record multiplicity. When a root table has no projected or
  filtered attribute, include its relevant approved key equality so the root
  remains represented. Do not deduplicate just because several records share
  the same projected value.
- Filter with column = string/integer literal predicates joined by `AND`.
  Preserve duplicate result rows unless distinct results are requested;
  use `SELECT DISTINCT` when requested. No result order is required.
- Each approved reference has a non-null source key and exactly one target,
  whose key is unique. Use the approved source-column = target-column facts
  when determining which relationships answer the question.
- These tasks do not require aggregates, GROUP BY, OR, NOT, ORDER BY, LIMIT,
  subqueries, set operators, or missing/optional-reference navigation. Do not
  add them. Use literal values rather than parameter placeholders.

Use exact schema names. This converter profile uses simple ASCII identifiers.
Strings use single quotes and doubled embedded single quotes, as in `'O''Neil'`;
backslashes are literal. Integers range from -9223372036854775808 to
9223372036854775807; write a negative sign adjacent to the digits. Do not use
real or NULL literals. Multiple roles reaching one physical table require
special care: table-qualified columns alone do not name a relationship role.

## Example

This example is separate from the supplied task. Schema:

- `EMPLOYEE(eid integer unique non-null, name string, department_id integer non-null)`
- `DEPARTMENT(did integer unique non-null, name string, region string)`
- Approved reference `EMPLOYEE.department` uses
  `EMPLOYEE.department_id = DEPARTMENT.did`, with exactly one target per employee.

Question: List the employee name and department name for employee identifier 7
when that employee's department is in region West.

```sql
SELECT EMPLOYEE.name, DEPARTMENT.name
FROM EMPLOYEE
WHERE DEPARTMENT.region = 'West' AND EMPLOYEE.eid = 7;
```
