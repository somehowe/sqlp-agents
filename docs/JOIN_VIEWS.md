# SQL with catalog-generated join views

This is a separate engineering control for asking whether SQLP's benefit comes
from moving join construction into software. It is not a reproduction of
workload-driven view recommendation or automatic relationship discovery.

```bash
python3 -m tools.prepare_join_views \
  build/chinook-study/snapshot.sqlite build/chinook-study/catalog.json \
  build/chinook-join-views --depth 3
```

The output directory must be new. It contains `snapshot.sqlite`, `catalog.json`,
`view_guide.md`, and `metadata.json`. The metadata is written last and marks a
complete artifact. The original database is opened read-only and remains
unchanged. Files are owner-readable inside a private directory.

For each root table, the builder expands **all** declared reference prefixes
through the chosen fixed depth, then reuses the C++ compiler to emit its SELECT.
It takes no workload, questions, prepared queries, or answers. Finite cycles are
unrolled to the depth bound. Each view retains one row per root record; total,
non-null references to unique targets make the INNER JOIN expansion valid.
No filtering, aggregation, or DISTINCT is added. Actual types, keys, references,
and root/view row counts are checked before publication.

Flattened columns preserve role prefixes, such as `track__album__Title`.
Underscores are escaped and case collisions receive deterministic suffixes;
the metadata records exact structured paths, terminal columns, and join traces.
Only direct root keys retain unique flags. A reached entity's key can repeat
across root rows, so those projected columns are not marked unique.

The exported catalog contains the original tables/references plus the view
tables. Append the same language-neutral `view_guide.md` to the corresponding
SQL and SQLP instructions for their view-control conditions. Both languages
can choose a generated view root and access its ordinary flattened columns.
This permits a two-by-two schema-exposure/language control. Count the full
schema and guide tokens.
Do not give an oracle-selected view or task-specific path hints. The model can
choose views or original tables; inspect actual generated SQL to record usage.
Existing SQL/SQLP workloads can still supply scoring answers because the added
views leave their original tables unchanged. No model calls occur in preparation.

This prototype allows depths 1–3, at most 63 joins and 2,000 columns **per view**,
and ASCII identifiers up to 64 characters in the source catalog. If any root
exceeds a bound, preparation fails instead of silently dropping that root.
Longer paths still require base-table joins; schema/guide growth is a measured
cost of this control. Use deterministic tables/views and approved data only.
