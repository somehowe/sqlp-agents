# NatSQL query guide

Answer the supplied question with one NatSQL SELECT statement. Return only the
query, ending with a semicolon; do not return Markdown or an explanation. Use
only the supplied schema and approved relationship facts.

## Supported task profile

- Write column references as `TABLE.column`, using the physical table names.
  These are not SQLP reference paths. Do not invent column or table aliases.
- Include `FROM TABLE`, as required by this converter's parser. NatSQL infers
  the SQL tables and joins from the columns in SELECT and WHERE plus the supplied
  keys; FROM alone does not force a table into the inferred query.
  Do not write explicit JOIN clauses. A declared source-column = target-column
  equality can make a relationship explicit.
- For existence questions, NatSQL supports a flat column-IN-column condition:
  `PARENT.key IN CHILD.parent_key AND CHILD.attribute = 'value'`. The converter
  reconstructs the required SQL subquery and its joins. Use the supplied key
  mapping; do not put a parenthesized SELECT after IN. This native NatSQL form
  is useful when a question follows a long chain of relationships.
- Within each query block, let the converter infer foreign-key joins from the
  columns used in SELECT and filters. A flat `left_column IN right_column`
  starts a separate membership subquery; do not use IN as a replacement for
  every foreign-key join. Add another IN only when the requested meaning needs
  another membership subquery.
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
  explicit nested SELECT statements, set operators, or missing/optional-reference
  navigation. Do not add them. Flat column-IN-column conditions are allowed;
  the converter may generate a nested SELECT. Use literal values rather than
  parameter placeholders.

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

## Existence example

Using the same separate example schema, list department names with at least one
employee named Ada. Return each name once.

```sql
SELECT DISTINCT DEPARTMENT.name
FROM DEPARTMENT
WHERE DEPARTMENT.did IN EMPLOYEE.department_id AND EMPLOYEE.name = 'Ada';
```

The IN operands use the declared employee-to-department key mapping in reverse
for this existence test. NatSQL infers the SQL subquery; this is not a SQLP path.
