# Historical plan: October 14–18, 2026

These early compiler tasks are now complete. See the [README](README.md) for the
current implementation and the [shared schedule](docs/SCHEDULES/SCHEDULE.md)
for remaining work.

- [x] Fix the empty-vector test so it fails properly if an invalid path is accepted.
- [x] Add one-hop path resolution, connecting reference lookup and JOIN rendering.
- [x] Generate the complete SQL query for the AUTHOR/PAPER/WROTE example from C++ objects.
- [x] Represent joins and columns as structured data, with a catalog for tables, column types, keys, and references.
