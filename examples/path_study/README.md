# Controlled relationship-path study

This is a synthetic development workload for separating relationship depth,
competing roles, and result multiplicity. It is project-authored, not an external
benchmark or data lake. Expected answers still need human interpretation review.

From the repository root:

```bash
make
python3 -m tools.prepare_path_study build/path-study
python3 -m tools.prepare_path_study build/path-study-wide --distractor-tables 16
```

Each command needs a new output directory. Preparation makes no model calls and
does not read credentials. A failed preparation may leave an incomplete folder;
keep the diagnostic and choose a new path after correcting it.

## What is varied

`Assignment` has requester, approver, reviewer, and owner references to `Person`.
`Person` has manager, mentor, and reviewer references back to `Person`. Every
reference is non-null and total, with a unique target key. A manager cycle and
other finite paths revisit the same table. Ordinary names and labels repeat.
Both tables have country attributes, so the requested relationship matters.

There are **32 related question templates**: eight each at reference depths 1,
2, 4, and 6. They cover projections, country filters, two role chains, `AND`,
duplicate-preserving answers, and `DISTINCT`. Each depth uses the same eight
families with the same predicate values and distinctness policy; only the role
chain changes. Some filters have empty answers on individual snapshots.
Longer paths are explicitly stated in plain English. They are constructed
questions rather than an estimate of naturally occurring query frequency.

The default output has a primary snapshot and two contrast snapshots generated
from different seeds. All share the same schema, questions, and reference
queries, while records and expected answers change. A Python dictionary walk
computes expected row bags without executing a generated join query. Both the
prepared SQL and compiled SQLP must match it on every snapshot before a manifest
is written.

An optional 0–64 archive-table setting adds unused schema/data/relationships.
The core assignment/person records and questions stay identical for a given
seed. Both languages must receive the same complete chosen catalog. This
supports a context-size comparison without changing the actual query tasks.
The large schemas are outside the optional NatSQL adapter's declared profile.

## Files and use

| File | Purpose |
|---|---|
| `snapshot.sqlite`, `catalog.json`, `development.json` | Primary database and runner inputs |
| `contrast-1/`, `contrast-2/` | Alternate database/workload pairs; reuse the root catalog |
| `oracle-plans.json` | Private reference interpretation; never send it to a model |
| `prepared-report.json` | Passing manual execution records, with no model usage |
| `manifest.json` | Seeds, checksums, limits of interpretation, and contrast sensitivity |

Use the ordinary experiment kit and pilot with the primary files. To check
semantic robustness, execute each already-generated query on the alternate
snapshots and score against their corresponding expected bags. This is new
execution evidence, not another model attempt. Preserve primary and contrast
scores separately; never send expected answers back to the model.

The three snapshots are not three independent sets of natural-language tasks.
The templates share a schema and construction, so model results need descriptive
reporting and do not support broad significance or real-world generalization.

## A known observability limit

For the default three seeds, swapping every requester role with approver changes
the answer on at least one snapshot for **all 32** templates. Some wrong-role
queries happen to match one snapshot; contrast checking makes them detectable.
The manifest recomputes this probe for the selected seeds. It is a test of one
specified mistake, not proof that all incorrect queries will be detected.
